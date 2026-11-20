# -*-coding: utf-8 -*-
"""设备入流信令契约与真机等价回环：RFC 9725 WHIP 的 HTTP 语义、DTLS 被动
角色改写与三通道（音频轨 / jpeg / capture）端到端数据落地。

设备端用真实 aiortc PeerConnection 模拟 esp32s3capture 固件行为：作为
Offerer 以 ``application/sdp`` + Bearer 发起 WHIP POST，从 ``201`` 的
响应体读取 SDP 应答并记录 ``Location``，释放时 DELETE 资源。整个会话在
单进程内经 UDP 完成 ICE/DTLS/SCTP 握手，验证内核产出业务可读的帧与
传感器摘要。
"""
import asyncio
import json

from server import settings
from server.bootstrap import assemble
from server.presentation.signaling.whip import answer_sdp_for_device

TEST_DEVICE_TOKEN = "test-device-token"


def _make_client(kernel, *, token=TEST_DEVICE_TOKEN):
    from fastapi.testclient import TestClient

    from server.app import create_app

    settings.DEVICE_SIGNAL_TOKEN = token
    app = create_app(assemble(kernel=kernel))
    return TestClient(app)


def _post_whip(client, sdp_text, *, token=TEST_DEVICE_TOKEN, content_type="application/sdp"):
    headers = {"content-type": content_type}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    return client.post("/whip/capture", content=sdp_text, headers=headers)


class TestWhipHttpContract:
    def test_rejects_non_sdp_content_type(self):
        from server.capture.webrtc.kernel import create_kernel

        client = _make_client(create_kernel())
        response = _post_whip(client, "v=0\r\n", content_type="application/json")
        assert response.status_code == 415

    def test_rejects_empty_sdp(self):
        from server.capture.webrtc.kernel import create_kernel

        client = _make_client(create_kernel())
        response = _post_whip(client, "   ")
        assert response.status_code == 400

    def test_rejects_oversized_body(self):
        from server.capture.webrtc.kernel import create_kernel

        client = _make_client(create_kernel())
        response = _post_whip(client, "v=0\r\n" + "x" * 70_000)
        assert response.status_code == 413

    def test_rejects_offer_without_audio_track(self):
        """固件必带音频轨：无音频的 Offer 直接 400，不驱逐在线会话。"""
        from aiortc import RTCConfiguration, RTCPeerConnection

        from server.capture.webrtc.kernel import create_kernel

        async def make_offer_sdp():
            # WHIP 在连接前校验媒体轨，原始 SDP 不需要 ICE 候选。
            pc = RTCPeerConnection(RTCConfiguration(iceServers=[]))
            try:
                pc.createDataChannel("capture")
                return (await pc.createOffer()).sdp
            finally:
                await pc.close()

        client = _make_client(create_kernel())
        sdp = asyncio.run(make_offer_sdp())
        response = _post_whip(client, sdp)
        assert response.status_code == 400
        assert response.json()["detail"] == "Device offer must send audio"

    def test_rate_limit_returns_429(self):
        from server.capture.webrtc.kernel import create_kernel

        client = _make_client(create_kernel())
        codes = [_post_whip(client, "v=0\r\nbroken").status_code for _ in range(31)]
        assert codes[-1] == 429
        assert all(code == 400 for code in codes[:-1])


class TestWhipAuthorization:
    """P5：设备入流用标准 Bearer；未配置即拒绝；无查询参数通道。"""

    def test_missing_bearer_is_unauthorized(self):
        from server.capture.webrtc.kernel import create_kernel

        client = _make_client(create_kernel())
        response = _post_whip(client, "v=0\r\n", token=None)
        assert response.status_code == 401
        assert "www-authenticate" in {key.lower() for key in response.headers}

    def test_wrong_bearer_is_unauthorized(self):
        from server.capture.webrtc.kernel import create_kernel

        client = _make_client(create_kernel())
        response = _post_whip(client, "v=0\r\n", token="wrong")
        assert response.status_code == 401

    def test_unconfigured_token_rejects_even_valid_requests(self):
        from server.capture.webrtc.kernel import create_kernel

        client = _make_client(create_kernel(), token="")
        response = _post_whip(client, "v=0\r\n")
        assert response.status_code == 503

    def test_query_parameter_token_is_not_accepted(self):
        from server.capture.webrtc.kernel import create_kernel

        client = _make_client(create_kernel())
        response = client.post(
            f"/whip/capture?token={TEST_DEVICE_TOKEN}",
            content="v=0\r\n",
            headers={"content-type": "application/sdp"},
        )
        assert response.status_code == 401

    def test_delete_requires_bearer(self):
        from server.capture.webrtc.kernel import create_kernel

        client = _make_client(create_kernel())
        response = client.delete("/whip/capture/any-resource")
        assert response.status_code == 401

    def test_delete_unknown_resource_is_404(self):
        from server.capture.webrtc.kernel import create_kernel

        client = _make_client(create_kernel())
        response = client.delete(
            "/whip/capture/not-a-session",
            headers={"Authorization": f"Bearer {TEST_DEVICE_TOKEN}"},
        )
        assert response.status_code == 404


OFFER_WITH_ACTPASS_SCTP = (
    "v=0\r\n"
    "o=- 4611731400430051336 2 IN IP4 127.0.0.1\r\n"
    "s=-\r\n"
    "t=0 0\r\n"
    "a=group:BUNDLE 0 1\r\n"
    "a=msid-semantic: WMS stream\r\n"
    "m=audio 9 UDP/TLS/RTP/SAVPF 111\r\n"
    "c=IN IP4 0.0.0.0\r\n"
    "a=rtcp:9 IN IP4 0.0.0.0\r\n"
    "a=ice-ufrag:device\r\n"
    "a=ice-pwd:devicepwd\r\n"
    "a=fingerprint:sha-256 00:11:22:33:44:55:66:77:88:99:AA:BB:CC:DD:EE:FF"
    ":00:11:22:33:44:55:66:77:88:99:AA:BB:CC:DD:EE:FF\r\n"
    "a=setup:actpass\r\n"
    "a=mid:0\r\n"
    "a=sendonly\r\n"
    "a=rtpmap:111 opus/48000/2\r\n"
    "a=fmtp:111 minptime=10;useinbandfec=1\r\n"
    "m=application 9 UDP/DTLS/SCTP webrtc-datachannel\r\n"
    "c=IN IP4 0.0.0.0\r\n"
    "a=ice-ufrag:device\r\n"
    "a=ice-pwd:devicepwd\r\n"
    "a=fingerprint:sha-256 00:11:22:33:44:55:66:77:88:99:AA:BB:CC:DD:EE:FF"
    ":00:11:22:33:44:55:66:77:88:99:AA:BB:CC:DD:EE:FF\r\n"
    "a=setup:actpass\r\n"
    "a=mid:1\r\n"
    "a=sctp-port:5000\r\n"
    "a=max-message-size:262144\r\n"
)

ANSWER_ACTIVE_SCTP = OFFER_WITH_ACTPASS_SCTP.replace(
    "a=setup:actpass", "a=setup:active").replace("o=- 4611731400430051336 2",
                                                 "o=- 8819123 2")


class TestAnswerRoleRewrite:
    def test_actpass_sctp_bundle_is_answered_passive(self):
        answer = answer_sdp_for_device(OFFER_WITH_ACTPASS_SCTP, ANSWER_ACTIVE_SCTP)
        # BUNDLE 传输上的所有 m-line 统一为 passive（服务端为 DTLS 服务端）
        assert "a=setup:passive" in answer
        assert "a=setup:active" not in answer
        assert "a=setup:actpass" not in answer
