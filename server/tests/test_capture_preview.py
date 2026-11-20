"""下行浏览器 WebRTC 预览与采集方向归一化契约。"""

import asyncio
import json
import time

import cv2
import httpx
import numpy as np
import pytest
from aioice import ice
from aiortc import RTCPeerConnection, RTCConfiguration
from aiortc.mediastreams import MediaStreamError
from fastapi import FastAPI

from types import SimpleNamespace

from server.capture.webrtc.jpeg import OpenCvCanonicalFrameDecoder
from server.capture.webrtc.kernel import CaptureKernel
from server.presentation.preview import (
    CaptureVideoTrack,
    create_preview_router,
)
import server.presentation.preview as preview_module
from server.support.identity.http_dependencies import IdentityDependencies


class _Subscription:
    async def next_frame(self, _timeout):
        return None

    def close(self):
        return None


def _jpeg_bytes(size=(6, 4), top_left=(1, 2, 3)):
    frame = np.zeros((size[1], size[0], 3), dtype=np.uint8)
    frame[0, 0] = top_left
    ok, encoded = cv2.imencode(".jpg", frame)
    assert ok
    return encoded.tobytes()


class _Window:
    """非空视频窗口替身：预览连接门槛只判空，不解码。"""



IDENTITY_TOKEN = "test-identity-token"


class _FakeIdentity:
    def principal_from_token(self, token):
        if token != IDENTITY_TOKEN:
            from server.support.identity.types.errors import IdentityError
            raise IdentityError("invalid token")
        return SimpleNamespace(user_id=1)


_IDENTITY_DEPS = IdentityDependencies(_FakeIdentity())


def _make_app(router):
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    return app


class _Source:
    def __init__(self):
        self.window = _Window()

    def is_connected(self):
        return True

    def video_window(self, seconds=None):
        return self.window

    def subscribe_video(self):
        return _Subscription()


@pytest.fixture
def browser_offer(monkeypatch):
    # 这些用例只交换 SDP，保留真实 peer 的状态机，无需网卡或 UDP。
    monkeypatch.setattr(ice, "get_host_addresses", lambda **kwargs: [])

    async def make_offer():
        peer = RTCPeerConnection(RTCConfiguration(iceServers=[]))
        peer.addTransceiver("video", direction="recvonly")
        offer = await peer.createOffer()
        await peer.setLocalDescription(offer)
        return peer, {"type": peer.localDescription.type, "sdp": peer.localDescription.sdp}

    return make_offer


async def _open_session(client):
    response = await client.post(
        "/api/v1/capture/preview/session",
        headers={"Authorization": f"Bearer {IDENTITY_TOKEN}"},
    )
    assert response.status_code == 200
    session = response.json()
    return {
        "preview_session_id": session["preview_session_id"],
        "headers": {"Authorization": f"Bearer {session['token']}"},
    }


async def _post_offer(client, offer):
    session = await _open_session(client)
    return await client.post(
        "/api/v1/capture/preview/offer",
        json={**offer, "preview_session_id": session["preview_session_id"]},
        headers=session["headers"],
    )


def test_preview_offer_returns_answer(browser_offer):
    async def run():
        source = _Source()
        router, manager = create_preview_router(source, OpenCvCanonicalFrameDecoder(), principal=_IDENTITY_DEPS.current_principal)
        app = _make_app(router)
        transport = httpx.ASGITransport(app=app)
        browser, offer = await browser_offer()
        try:
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                response = await _post_offer(client, offer)
            assert response.status_code == 200
            answer = response.json()
            assert answer["type"] == "answer"
            assert "m=video" in answer["sdp"]
        finally:
            await manager.close()
            await browser.close()

    asyncio.run(run())


def test_preview_offer_admits_up_to_the_connection_cap(browser_offer):
    async def run():
        router, manager = create_preview_router(_Source(), OpenCvCanonicalFrameDecoder(), principal=_IDENTITY_DEPS.current_principal, max_peers=1)
        app = _make_app(router)
        transport = httpx.ASGITransport(app=app)
        first_browser, offer = await browser_offer()
        second_browser, _ = await browser_offer()
        try:
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                accepted = await _post_offer(client, offer)
                assert accepted.status_code == 200
                rejected = await _post_offer(client, offer)
                assert rejected.status_code == 429
            await manager.close_peer(next(iter(manager._peers)))
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                readmitted = await _post_offer(client, offer)
                assert readmitted.status_code == 200
        finally:
            await manager.close()
            await first_browser.close()
            await second_browser.close()

    asyncio.run(run())


def test_stale_preview_track_closes_peer_and_releases_subscription(browser_offer):
    async def run():
        router, manager = create_preview_router(_Source(), OpenCvCanonicalFrameDecoder(), principal=_IDENTITY_DEPS.current_principal)
        app = _make_app(router)
        browser, offer = await browser_offer()
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test",
            ) as client:
                response = await _post_offer(client, offer)
            assert response.status_code == 200
            peer = next(iter(manager._peers))
            track = manager._tracks[peer]
            closed = asyncio.Event()

            @peer.on("connectionstatechange")
            def on_state_change():
                if peer.connectionState == "closed":
                    closed.set()

            with pytest.raises(MediaStreamError):
                await track.recv()
            await asyncio.wait_for(closed.wait(), timeout=1)
            assert not manager._peers
            assert not manager._tracks
            assert track.readyState == "ended"
        finally:
            await manager.close()
            await browser.close()

    asyncio.run(run())


def test_never_connected_preview_peer_is_released_within_budget(monkeypatch, browser_offer):
    # 连接预算是模块常量且在调用点读取：缩短到 0.5s 验证同一释放行为，
    # 不用真等 10s（原实现是测试里最慢的单项）。
    monkeypatch.setattr(preview_module, "PREVIEW_CONNECT_TIMEOUT_SECONDS", 0.5)

    async def run():
        router, manager = create_preview_router(_Source(), OpenCvCanonicalFrameDecoder(), principal=_IDENTITY_DEPS.current_principal)
        app = _make_app(router)
        browser, offer = await browser_offer()
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test",
            ) as client:
                response = await _post_offer(client, offer)
            assert response.status_code == 200
            peer = next(iter(manager._peers))
            guard = manager._guards[peer]
            closed = asyncio.Event()

            @peer.on("connectionstatechange")
            def on_state_change():
                if peer.connectionState == "closed":
                    closed.set()

            # 没有 ICE 候选的对端保持未连接，连接预算到期后释放。
            await asyncio.wait_for(closed.wait(), timeout=2)
            await asyncio.wait_for(guard, timeout=2)
            assert not manager._peers
            assert not manager._guards
        finally:
            await manager.close()
            await browser.close()

    asyncio.run(run())


def test_capture_video_track_emits_newest_canonical_frame():
    jpeg = _jpeg_bytes(size=(6, 4))

    class Subscription(_Subscription):
        async def next_frame(self, _timeout):
            return jpeg, time.monotonic(), 1

    async def run():
        track = CaptureVideoTrack(Subscription(), OpenCvCanonicalFrameDecoder())
        output = await track.recv()
        try:
            # 6×4（宽×高）原始画面经规范方向归一化后为 4×6（宽×高，转置）
            assert output.to_ndarray(format="bgr24").shape == (6, 4, 3)
        finally:
            track.stop()

    asyncio.run(run())


def test_capture_video_track_skips_undecodable_frames():
    good = _jpeg_bytes(size=(6, 4))

    class Subscription(_Subscription):
        def __init__(self):
            self.items = [b"not-a-jpeg", good]
            self.calls = 0

        async def next_frame(self, _timeout):
            if self.calls >= len(self.items):
                return None
            item = self.items[self.calls]
            self.calls += 1
            return item, time.monotonic(), self.calls

    async def run():
        track = CaptureVideoTrack(Subscription(), OpenCvCanonicalFrameDecoder())
        output = await track.recv()
        try:
            assert output.to_ndarray(format="bgr24").shape == (6, 4, 3)
        finally:
            track.stop()

    asyncio.run(run())


def test_video_subscription_skips_stale_duplicates_and_returns_raw_jpeg():
    jpeg = _jpeg_bytes(size=(4, 6))

    async def run():
        kernel = CaptureKernel()
        subscription = kernel.subscribe_video()
        kernel.publish_frame(jpeg, time.monotonic(), time.monotonic())
        first = await subscription.next_frame(timeout=0.05)
        assert first is not None
        assert first[0] == jpeg  # 订阅交付原始字节，解码留给消费方
        assert await subscription.next_frame(timeout=0.01) is None
        subscription.close()
        await kernel.close()

    asyncio.run(run())


def test_generation_replacement_terminates_active_subscriptions():
    jpeg = _jpeg_bytes()

    async def run():
        kernel = CaptureKernel()
        subscription = kernel.subscribe_video()
        kernel.publish_frame(jpeg, time.monotonic(), time.monotonic())
        assert await subscription.next_frame(timeout=0.05) is not None
        # 新会话发布即终止旧代际订阅；后续新代际帧不再投递给旧订阅。
        kernel.on_session_published(None)
        assert await subscription.next_frame(timeout=0.01) is None
        kernel.publish_frame(jpeg, time.monotonic(), time.monotonic())
        assert await subscription.next_frame(timeout=0.01) is None
        assert kernel._subscriptions == set()
        await kernel.close()

    asyncio.run(run())


def test_video_window_decodes_in_canonical_orientation():
    raw = np.zeros((64, 96, 3), dtype=np.uint8)
    raw[:32, :48] = (255, 0, 0)
    raw[:32, 48:] = (0, 255, 0)
    raw[32:, :48] = (0, 0, 255)
    raw[32:, 48:] = (0, 255, 255)
    ok, encoded = cv2.imencode(".jpg", raw)
    assert ok
    kernel = CaptureKernel()
    kernel.publish_frame(encoded.tobytes(), time.monotonic(), time.monotonic())
    window = kernel.video_window()
    assert window is not None
    output = next(iter(window)).data
    asyncio.run(kernel.close())
    assert output.shape[:2] == (96, 64)
    # JPEG 有损压缩容许小幅色差，四象限位置必须保持规范方向。
    actual = output[[24, 24, 72, 72], [16, 48, 16, 48]].astype(int)
    expected = np.array([(0, 255, 255), (0, 255, 0), (0, 0, 255), (255, 0, 0)])
    assert np.max(np.abs(actual - expected)) < 20


def test_preview_offer_requires_a_valid_single_use_session(browser_offer):
    async def run():
        router, manager = create_preview_router(_Source(), OpenCvCanonicalFrameDecoder(), principal=_IDENTITY_DEPS.current_principal)
        app = _make_app(router)
        transport = httpx.ASGITransport(app=app)
        browser, offer = await browser_offer()
        try:
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                # 无令牌 / 错误会话 / 错误令牌 → 401（缺字段属形状错误，422 由框架层拦截）
                missing = await client.post(
                    "/api/v1/capture/preview/offer",
                    json={**offer, "preview_session_id": "any-session"},
                )
                assert missing.status_code == 401
                session = (await client.post(
                    "/api/v1/capture/preview/session",
                    headers={"Authorization": f"Bearer {IDENTITY_TOKEN}"},
                )).json()
                wrong_sid = await client.post(
                    "/api/v1/capture/preview/offer", json={
                        **offer, "preview_session_id": "not-a-session"},
                    headers={"Authorization": f"Bearer {session['token']}"})
                assert wrong_sid.status_code == 401
                wrong_token = await client.post(
                    "/api/v1/capture/preview/offer", json={
                        **offer, "preview_session_id": session["preview_session_id"]},
                    headers={"Authorization": "Bearer wrong"})
                assert wrong_token.status_code == 401

                # 有效会话成功，且令牌单次使用：重放 401
                ok = await client.post(
                    "/api/v1/capture/preview/offer", json={
                        **offer, "preview_session_id": session["preview_session_id"]},
                    headers={"Authorization": f"Bearer {session['token']}"})
                assert ok.status_code == 200
                replay = await client.post(
                    "/api/v1/capture/preview/offer", json={
                        **offer, "preview_session_id": session["preview_session_id"]},
                    headers={"Authorization": f"Bearer {session['token']}"})
                assert replay.status_code == 401

                # 取消端点使未使用会话失效
                cancel_session = (await client.post(
                    "/api/v1/capture/preview/session",
                    headers={"Authorization": f"Bearer {IDENTITY_TOKEN}"},
                )).json()
                cancelled = await client.delete(
                    f"/api/v1/capture/preview/session/{cancel_session['preview_session_id']}",
                    headers={"Authorization": f"Bearer {IDENTITY_TOKEN}"},
                )
                assert cancelled.status_code == 200
                after_cancel = await client.post(
                    "/api/v1/capture/preview/offer", json={
                        **offer, "preview_session_id": cancel_session["preview_session_id"]},
                    headers={"Authorization": f"Bearer {cancel_session['token']}"})
                assert after_cancel.status_code == 401
        finally:
            await manager.close()
            await browser.close()

    asyncio.run(run())


def test_preview_session_issuance_is_rate_limited_and_expires():
    async def run():
        router, manager = create_preview_router(_Source(), OpenCvCanonicalFrameDecoder(), principal=_IDENTITY_DEPS.current_principal)
        app = _make_app(router)
        transport = httpx.ASGITransport(app=app)
        try:
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                codes = []
                for _ in range(11):
                    codes.append((await client.post(
                        "/api/v1/capture/preview/session",
                        headers={"Authorization": f"Bearer {IDENTITY_TOKEN}"},
                    )).status_code)
                assert codes.count(200) == 10 and codes[-1] == 429

        finally:
            await manager.close()

    asyncio.run(run())


def test_expired_preview_session_is_rejected():
    async def run():
        router, manager = create_preview_router(_Source(), OpenCvCanonicalFrameDecoder(), principal=_IDENTITY_DEPS.current_principal)
        app = _make_app(router)
        transport = httpx.ASGITransport(app=app)
        try:
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                session = (await client.post(
                    "/api/v1/capture/preview/session",
                    headers={"Authorization": f"Bearer {IDENTITY_TOKEN}"},
                )).json()
                manager._sessions[session["preview_session_id"]]["expires_at"] = (
                    time.monotonic() - 1)
                expired = await client.post(
                    "/api/v1/capture/preview/offer", json={
                        "type": "offer", "sdp": "v=0",
                        "preview_session_id": session["preview_session_id"]},
                    headers={"Authorization": f"Bearer {session['token']}"})
                assert expired.status_code == 401
        finally:
            await manager.close()

    asyncio.run(run())


def test_oversized_offer_is_rejected_before_json_decode():
    """体积门在框架 JSON 解码之前执行：两种超限请求都 413，内容不回显。

    锁定的泄露输入（评审复现：约 64 KiB 的超限体曾以 422 回显；依赖级
    门保护不了畸形 JSON 的解析阶段——自定义路由类把检查提到解码之前）。
    """
    async def run():
        router, manager = create_preview_router(
            _Source(), OpenCvCanonicalFrameDecoder(), principal=_IDENTITY_DEPS.current_principal)
        app = _make_app(router)
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test",
            ) as client:
                for name, body in (
                    ("valid-json", json.dumps({
                        "type": "offer", "sdp": "v=0\r\n" + "x" * (64 * 1024),
                        "preview_session_id": "any-session"}).encode()),
                    ("malformed-json",
                     b'{"type":"offer","sdp":"' + b"y" * (64 * 1024) + b"broken,"),
                ):
                    session = (await client.post(
                        "/api/v1/capture/preview/session",
                        headers={"Authorization": f"Bearer {IDENTITY_TOKEN}"},
                    )).json()
                    oversized = await client.post(
                        "/api/v1/capture/preview/offer", content=body,
                        headers={
                            "Content-Type": "application/json",
                            "Authorization": f"Bearer {session['token']}",
                        })
                    assert oversized.status_code == 413, (name, oversized.status_code)
                    assert oversized.json() == {"detail": "Preview offer too large"}, name
        finally:
            await manager.close()

    asyncio.run(run())


def test_preview_session_issuance_is_guest_accessible_and_cancel_needs_identity():
    """预览会话签发使用独立的短期令牌（游客可取）；取消仍归登录身份。"""
    async def run():
        router, manager = create_preview_router(_Source(), OpenCvCanonicalFrameDecoder(), principal=_IDENTITY_DEPS.current_principal)
        app = _make_app(router)
        transport = httpx.ASGITransport(app=app)
        try:
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                guest = await client.post("/api/v1/capture/preview/session")
                assert guest.status_code == 200
                session_id = guest.json()["preview_session_id"]

                unauthenticated = await client.delete(
                    f"/api/v1/capture/preview/session/{session_id}")
                assert unauthenticated.status_code == 401

                valid = await client.delete(
                    f"/api/v1/capture/preview/session/{session_id}",
                    headers={"Authorization": f"Bearer {IDENTITY_TOKEN}"},
                )
                assert valid.status_code == 200
        finally:
            await manager.close()

    asyncio.run(run())


def test_subscription_waits_past_stale_frame_and_recovers_on_new_publication():
    now = [100.0]
    kernel = CaptureKernel(clock=lambda: now[0])
    jpeg = _jpeg_bytes()
    kernel.publish_frame(jpeg, now[0], now[0])
    now[0] += 2.0

    async def run():
        subscription = kernel.subscribe_video()
        try:
            assert await subscription.next_frame(timeout=0.01) is None
            waiting = asyncio.create_task(subscription.next_frame(timeout=0.1))
            await asyncio.sleep(0)
            assert not waiting.done()
            kernel.publish_frame(jpeg, now[0], now[0])
            assert (await waiting)[:2] == (jpeg, now[0])
        finally:
            subscription.close()
            await kernel.close()

    asyncio.run(run())
