"""浏览器设备预览的下行 WebRTC 适配。

设备上行 WebRTC 会话由 capture 持有；本模块为每个浏览器创建独立的
recv-only 视频 PeerConnection，从 capture 的有界最新帧订阅取得原始 JPEG
字节，在线程池中解码为规范 BGR 帧后推入 WebRTC，与报告密集推理彻底
物理隔离。
"""

import asyncio
import hmac
import json
import secrets
import time
import uuid
from collections import deque
from fractions import Fraction

from aiortc import RTCConfiguration, RTCPeerConnection, RTCSessionDescription, sdp
from aiortc.mediastreams import MediaStreamError, VideoStreamTrack
from av import VideoFrame
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from fastapi.routing import APIRoute
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field

from server.envelope import ErrorResponse


class _PreviewModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PreviewSessionIssued(_PreviewModel):
    """公开签发短期预览会话，浏览器持会话令牌完成 offer 协商。"""

    preview_session_id: str
    token: str
    expires_in: int


class PreviewSessionCancelled(_PreviewModel):
    cancelled: str


class PreviewOffer(_PreviewModel):
    """浏览器发起协商的请求体：形状由框架解析（模型校验），业务守卫
    （关闭态、大小、令牌、配额、SDP 深度解析、设备状态）留在管理器。"""

    type: Literal["offer"]
    sdp: str = Field(min_length=1)
    preview_session_id: str


class PreviewAnswer(_PreviewModel):
    """WebRTC 应答（SDP），按真实协议裸返回，不套业务封套。"""

    type: str
    sdp: str

from server.capture.types.frames import CanonicalFrameSource
from server.support.ratelimit import SlidingWindowRateLimiter

PREVIEW_OFFER_MAX_BYTES = 64 * 1024
PREVIEW_FRAME_TIMEOUT_SECONDS = 2.0
PREVIEW_CONNECT_TIMEOUT_SECONDS = 10.0
VIDEO_CLOCK_RATE = 90_000
# 浏览器下行连接、视频轨与编码器共用此准入上限；预览不阻塞设备接收。
MAX_PREVIEW_PEERS = 4
# 预览会话：短期握手令牌，单次使用；会话存活期等于其下行连接的存活期。
PREVIEW_SESSION_TTL_SECONDS = 60.0
MAX_PREVIEW_SESSIONS = 32
PREVIEW_SESSION_RATE_LIMIT = 10
PREVIEW_SESSION_RATE_WINDOW_SECONDS = 60.0

# 预览协商用独立于 Identity 的会话令牌：官方安全依赖负责 ``Authorization:
# Bearer`` 的提取与 OpenAPI 安全声明；auto_error=False 让缺失/形状错误收敛
# 为预览域 401（RFC 6750：带 ``WWW-Authenticate``）。
_preview_bearer = HTTPBearer(auto_error=False, scheme_name="PreviewSession",
                             description="预览会话握手令牌（preview/session 签发，单次使用）")


def preview_session_token(
    credentials: HTTPAuthorizationCredentials | None = Depends(_preview_bearer),
) -> str:
    """从官方 Bearer 依赖取出预览会话令牌，交管理器做单次使用校验。"""
    if credentials is None:
        raise HTTPException(status_code=401,
                            detail="Preview offer requires a bearer session token",
                            headers={"WWW-Authenticate": "Bearer"})
    return credentials.credentials


class PreviewOfferRoute(APIRoute):
    """预览 offer 端点专用路由类：原始请求体先按契约上限把守（413），
    再进入框架的 JSON 解码。

    FastAPI 在路由依赖之前就已读取并解码请求体——依赖级体积门无法保护
    解析阶段（超限的畸形 JSON 会先撞进框架 422）；自定义 APIRoute 把
    检查提到框架处理请求之前，两种超限请求（合法与畸形 JSON）都得到
    413 且不回显内容。字节读取本身无法避免（框架行为），网络边的硬
    上限由部署层（deploy/caddy 的 request_body max_size）负责。"""

    def get_route_handler(self):
        original = super().get_route_handler()

        async def size_checked(request: Request) -> Response:
            if len(await request.body()) > PREVIEW_OFFER_MAX_BYTES:
                raise HTTPException(status_code=413, detail="Preview offer too large")
            return await original(request)

        return size_checked


class CaptureVideoTrack(VideoStreamTrack):
    """订阅原始 JPEG 字节并在独立线程池解码为浏览器视频帧。"""

    kind = "video"

    def __init__(self, subscription, decoder, *, timeout=PREVIEW_FRAME_TIMEOUT_SECONDS):
        super().__init__()
        self._subscription = subscription
        self._decoder = decoder
        self._timeout = timeout
        self._base_timestamp = None

    async def recv(self):
        while self.readyState == "live":
            item = await self._subscription.next_frame(self._timeout)
            if item is None:
                break
            jpeg, captured_at, _sequence = item
            image = await asyncio.to_thread(self._decoder.decode, jpeg)
            if image is None:
                continue  # 坏帧跳过，继续等待下一帧
            frame = VideoFrame.from_ndarray(image, format="bgr24")
            if self._base_timestamp is None:
                self._base_timestamp = captured_at
            frame.pts = max(0, int((captured_at - self._base_timestamp) * VIDEO_CLOCK_RATE))
            frame.time_base = Fraction(1, VIDEO_CLOCK_RATE)
            return frame
        self.stop()
        raise MediaStreamError

    def stop(self):
        if self.readyState != "ended":
            self._subscription.close()
        super().stop()


class PreviewManager:
    """持有浏览器预览 PeerConnection 与短期预览会话，应用关闭时统一回收。

    会话模型（P0/P2）：``preview_session_id`` 由本管理器签发，握手令牌
    短期、单次使用；会话的存活期等于其下行连接的存活期，取消端点关闭
    对应连接。会话状态只在内存中，进程重启即失效。
    """

    def __init__(self, source: CanonicalFrameSource, decoder, *,
                 max_peers: int = MAX_PREVIEW_PEERS):
        self._source = source
        self._decoder = decoder
        self._max_peers = max_peers
        self._peers: set[RTCPeerConnection] = set()
        self._tracks: dict[RTCPeerConnection, CaptureVideoTrack] = {}
        self._guards: dict[RTCPeerConnection, asyncio.Task] = {}
        self._sessions: dict[str, dict] = {}
        # 每客户端滑动窗口准入；仅用于预览会话签发。
        self._session_issuer = SlidingWindowRateLimiter(
            PREVIEW_SESSION_RATE_LIMIT, PREVIEW_SESSION_RATE_WINDOW_SECONDS)
        self._closed = False

    def issue_session(self, client: str) -> dict:
        self._purge_expired_sessions()
        if not self._session_issuer.allow(client):
            raise HTTPException(status_code=429, detail="预览会话签发过于频繁，请稍后重试")
        if len(self._sessions) >= MAX_PREVIEW_SESSIONS:
            raise HTTPException(status_code=429, detail="预览会话数量已达上限，请稍后重试")
        sid = uuid.uuid4().hex
        token = secrets.token_urlsafe(32)
        expires_at = time.monotonic() + PREVIEW_SESSION_TTL_SECONDS
        self._sessions[sid] = {"token": token, "expires_at": expires_at, "peer": None}
        return {
            "preview_session_id": sid,
            "token": token,
            "expires_in": int(PREVIEW_SESSION_TTL_SECONDS),
        }

    def cancel_session(self, sid: str) -> None:
        session = self._sessions.pop(sid, None)
        if session is None:
            raise HTTPException(status_code=404, detail="预览会话不存在或已结束")
        peer = session.get("peer")
        if peer is not None:
            asyncio.ensure_future(self.close_peer(peer))

    def _consume_session(self, sid, token) -> dict:
        self._purge_expired_sessions()
        session = self._sessions.get(sid)
        if (session is None or session["token"] is None
                or not hmac.compare_digest(str(token), session["token"])):
            raise HTTPException(status_code=401, detail="Invalid or expired preview session",
                                headers={"WWW-Authenticate": "Bearer"})
        session["token"] = None
        return session

    def _purge_expired_sessions(self) -> None:
        now = time.monotonic()
        for sid in [sid for sid, item in self._sessions.items()
                    if item["peer"] is None and now >= item["expires_at"]]:
            del self._sessions[sid]

    def _forget_session_peer(self, peer) -> None:
        for session in self._sessions.values():
            if session["peer"] is peer:
                session["peer"] = None
                return

    async def close_peer(self, peer, track=None):
        track = self._tracks.pop(peer, track)
        if track is not None:
            track.stop()
        guard = self._guards.pop(peer, None)
        # The timeout guard also calls close_peer. Cancelling it here would
        # interrupt peer.close() and can cancel aioice's shared mDNS future.
        if guard is not None and guard is not asyncio.current_task():
            guard.cancel()
        self._peers.discard(peer)
        self._forget_session_peer(peer)
        await peer.close()

    async def _close_if_never_connected(self, peer):
        """ICE 迟迟未连通的预览对端按资源预算关闭，不无限占用准入额度。"""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + PREVIEW_CONNECT_TIMEOUT_SECONDS
        while True:
            state = peer.connectionState
            if state in ("connected", "failed", "closed"):
                return
            remaining = deadline - loop.time()
            if remaining <= 0:
                break
            await asyncio.sleep(min(0.25, remaining))
        if peer in self._peers and peer.connectionState != "connected":
            await self.close_peer(peer)

    async def close(self):
        self._closed = True
        peers = tuple(self._peers)
        await asyncio.gather(*(self.close_peer(peer) for peer in peers), return_exceptions=True)

    async def offer(self, offer: PreviewOffer, session_token: str):
        """业务守卫链；请求体形状、体积与 Bearer 凭证已在框架层把守。"""
        if self._closed:
            raise HTTPException(status_code=503, detail="预览服务正在关闭")
        session = self._consume_session(offer.preview_session_id, session_token)
        if len(self._peers) >= self._max_peers:
            raise HTTPException(status_code=429, detail="预览连接数已达上限，请稍后重试")
        remote_sdp_text = offer.sdp
        try:
            remote_sdp = sdp.SessionDescription.parse(remote_sdp_text)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Invalid SDP") from exc
        if not any(media.kind == "video" for media in remote_sdp.media):
            raise HTTPException(status_code=400, detail="Preview offer must contain a video track")
        if not self._source.is_connected():
            raise HTTPException(status_code=409, detail="采集设备离线：MCU 未连接")
        if self._source.video_window() is None:
            raise HTTPException(status_code=409, detail="当前没有新鲜可用的设备画面")

        peer = RTCPeerConnection(RTCConfiguration(iceServers=[]))
        self._peers.add(peer)
        self._guards[peer] = asyncio.create_task(self._close_if_never_connected(peer))
        session["peer"] = peer
        track = None
        accepted = False

        @peer.on("connectionstatechange")
        async def on_connectionstatechange():
            if (peer in self._peers
                    and peer.connectionState in {"failed", "closed"}):
                await self.close_peer(peer, track)

        try:
            subscription = self._source.subscribe_video()
            track = CaptureVideoTrack(subscription, self._decoder)
            self._tracks[peer] = track

            @track.on("ended")
            async def on_track_ended():
                # A stale/replaced capture subscription ends the sender while
                # ICE can remain connected. Close the preview peer as well so
                # the browser can negotiate a fresh subscription.
                if peer in self._peers:
                    await self.close_peer(peer)

            peer.addTrack(track)
            await peer.setRemoteDescription(
                RTCSessionDescription(sdp=remote_sdp_text, type="offer")
            )
            await peer.setLocalDescription(await peer.createAnswer())
            accepted = True
            return JSONResponse({
                "type": peer.localDescription.type,
                "sdp": peer.localDescription.sdp,
            })
        except (ValueError, AssertionError, KeyError) as exc:
            raise HTTPException(status_code=400, detail="Invalid preview offer") from exc
        finally:
            if not accepted:
                await self.close_peer(peer, track)


def create_preview_router(
    source: CanonicalFrameSource,
    decoder,
    *,
    principal=None,
    max_peers: int = MAX_PREVIEW_PEERS,
):
    """构建预览路由；预览会话签发允许游客访问。

    ``principal`` 保留为装配兼容参数。预览使用独立的短期会话令牌，
    不依赖 Identity 登录状态。
    """

    manager = PreviewManager(source, decoder, max_peers=max_peers)
    router = APIRouter(tags=["设备预览"])

    @router.post("/capture/preview/session", response_model=PreviewSessionIssued,
                 responses={
                     429: {"model": ErrorResponse, "description": "预览会话签发过于频繁或已达上限"},
                 })
    async def preview_session(request: Request):
        client = request.client.host if request.client else "unknown"
        return JSONResponse(manager.issue_session(client))

    @router.delete("/capture/preview/session/{sid}", response_model=PreviewSessionCancelled,
                   responses={
                       401: {"model": ErrorResponse, "description": "未登录或登录凭证无效"},
                       404: {"model": ErrorResponse, "description": "预览会话不存在或已结束"},
                   })
    async def preview_session_cancel(sid: str, user=Depends(principal)):
        manager.cancel_session(sid)
        return JSONResponse({"cancelled": sid})

    async def preview_offer(offer: PreviewOffer,
                            token: str = Depends(preview_session_token)):
        return await manager.offer(offer, token)

    # 显式注册而非装饰器：该版本 FastAPI 仅 add_api_route 暴露按端点的
    # 路由类覆盖——体积门必须在框架 JSON 解码之前执行。
    router.add_api_route(
        "/capture/preview/offer",
        preview_offer,
        methods=["POST"],
        response_model=PreviewAnswer,
        responses={
            400: {"model": ErrorResponse, "description": "SDP 解析失败或缺少视频轨"},
            401: {"model": ErrorResponse, "description": "预览会话令牌缺失或无效"},
            404: {"model": ErrorResponse, "description": "预览会话不存在或已结束"},
            409: {"model": ErrorResponse, "description": "设备离线或无新鲜画面"},
            413: {"model": ErrorResponse, "description": "offer 载荷过大"},
            429: {"model": ErrorResponse, "description": "预览连接数已达上限"},
            503: {"model": ErrorResponse, "description": "预览服务正在关闭"},
        },
        route_class_override=PreviewOfferRoute,
    )

    return router, manager


__all__ = [
    "CaptureVideoTrack",
    "PreviewOffer",
    "MAX_PREVIEW_PEERS",
    "PREVIEW_CONNECT_TIMEOUT_SECONDS",
    "PREVIEW_FRAME_TIMEOUT_SECONDS",
    "PREVIEW_SESSION_TTL_SECONDS",
    "PreviewManager",
    "create_preview_router",
]
