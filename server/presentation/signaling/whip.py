# -*-coding: utf-8 -*-
"""设备 WebRTC 入流信令：RFC 9725 WHIP 资源端点（P5 收口）。

设备是唯一 Offerer：
- ``POST /whip/capture``，``Content-Type: application/sdp``，请求体为完整
  （非 trickle）SDP offer；``Authorization: Bearer <设备令牌>``。
- 成功返回 ``201 Created``，``Location: /whip/capture/{resource_id}``，
  响应体为 ``application/sdp`` 应答；资源 ID 即采集会话 ID。
- ``DELETE /whip/capture/{resource_id}`` 释放会话（关闭 PeerConnection、
  通道与订阅）；未知或已结束的资源返回 ``404``。

DTLS 角色自洽：设备 Offer 的 SCTP m-line 为 ``actpass`` 时，aiortc 默认应答
``active`` 无法与 ESP32 SCTP 栈互通，必须改写为 ``passive``（服务端为
DTLS 服务端）。见 esp32s3capture docs/step-08。

旧 JSON ``POST /offer`` 与查询参数令牌已按 P5 撤除，不再是正式路径。
"""

import hmac
import logging
import time

from aiortc import RTCConfiguration, RTCPeerConnection, RTCSessionDescription, sdp
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from server import settings
from server.capture.service import SessionStateError
from server.support.ratelimit import SlidingWindowRateLimiter

logger = logging.getLogger("physiosense.capture")

WHIP_SDP_MAX_BYTES = 64 * 1024
WHIP_RATE_LIMIT = 30
WHIP_RATE_WINDOW_SECONDS = 60.0
SDP_CONTENT_TYPE = "application/sdp"

# 设备接入令牌与 Identity 令牌是两个信任域：官方安全依赖负责
# ``Authorization: Bearer`` 的提取与 OpenAPI 安全声明；auto_error=False 让
# 缺失/形状错误收敛为设备域 401（RFC 6750：带 ``WWW-Authenticate``）。
_device_bearer = HTTPBearer(auto_error=False, scheme_name="DeviceToken",
                            description="设备接入令牌（部署配置注入）")


def device_session(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_device_bearer),
) -> None:
    """设备入流授权：标准 ``Authorization: Bearer``，令牌由部署配置注入。

    未配置令牌视为部署缺陷（缺省关闭，不依赖"知道 URL"）；不接受查询
    参数令牌，也不提供迁移宽限（P5 已撤除）。
    """
    if not settings.DEVICE_SIGNAL_TOKEN:
        raise HTTPException(status_code=503, detail="设备信令未配置访问令牌")
    if (credentials is None
            or not hmac.compare_digest(credentials.credentials.strip(),
                                       settings.DEVICE_SIGNAL_TOKEN)):
        client = request.client.host if request.client else "unknown"
        logger.warning("设备信令授权失败：来源 %s", client)
        raise HTTPException(status_code=401, detail="Invalid device signaling token",
                            headers={"WWW-Authenticate": "Bearer"})


def read_whip_offer(request: Request, body: bytes) -> str:
    """WHIP 请求体契约：``application/sdp``、64 KiB 上限、非空 SDP 文本。"""
    content_type = (request.headers.get("content-type") or "").split(";", 1)[0]
    if content_type.strip().lower() != SDP_CONTENT_TYPE:
        raise HTTPException(status_code=415, detail=f"Expected {SDP_CONTENT_TYPE}")
    if len(body) > WHIP_SDP_MAX_BYTES:
        raise HTTPException(status_code=413, detail="Offer body too large")
    offer_sdp = body.decode("utf-8", errors="strict").strip()
    if not offer_sdp:
        raise HTTPException(status_code=400, detail="Empty SDP offer")
    return offer_sdp


def answer_sdp_for_device(offer_sdp: str, answer_sdp: str) -> str:
    """仅当 Offer 的 SCTP m-line 为 actpass 时，BUNDLE 传输统一选择被动角色。"""
    remote = sdp.SessionDescription.parse(offer_sdp)
    answer = sdp.SessionDescription.parse(answer_sdp)
    answer_by_mid = {media.rtp.muxId: media for media in answer.media
                     if media.rtp.muxId is not None}

    for offered in remote.media:
        if (offered.kind != "application"
                or (offered.sctp_port is None and not offered.sctpmap)
                or offered.dtls is None
                or offered.dtls.role != "auto"):
            continue
        mids = {offered.rtp.muxId} if offered.rtp.muxId is not None else set()
        for group in remote.group:
            if group.semantic == "BUNDLE" and offered.rtp.muxId in group.items:
                mids.update(group.items)
        if mids:
            matching = [answer_by_mid[mid] for mid in mids if mid in answer_by_mid]
        else:
            matching = [media for media in answer.media if media.kind == "application"]
        for media in matching:
            if media.dtls is not None:
                # aiortc 对共享 BUNDLE 传输的所有 m-line 使用同一 DTLS 角色，
                # 应答必须在该传输上保持一致。
                media.dtls.role = "server"

    return str(answer)


async def _publish_device_session(kernel, offer_sdp: str) -> tuple:
    """协商并发布设备会话；失败路径不驱逐在线会话。"""
    pc = RTCPeerConnection(RTCConfiguration(iceServers=[]))
    tracks = []
    session = None

    @pc.on("track")
    def on_track(track):
        if track.kind == "audio":
            tracks.append(track)

    @pc.on("connectionstatechange")
    async def state_changed():
        logger.info("设备 WebRTC 状态：%s", pc.connectionState)
        if pc.connectionState in ("failed", "closed"):
            if session is not None:
                await session.close()
            else:
                await pc.close()

    try:
        try:
            remote = RTCSessionDescription(sdp=offer_sdp, type="offer")
            media = sdp.SessionDescription.parse(remote.sdp)
            if not any(item.kind == "audio" and item.direction in ("sendonly", "sendrecv")
                       for item in media.media):
                raise HTTPException(status_code=400, detail="Device offer must send audio")
            await pc.setRemoteDescription(remote)
            if len(tracks) != 1:
                raise HTTPException(
                    status_code=400, detail="Device must send one audio track")
            answer = await pc.createAnswer()
            await pc.setLocalDescription(RTCSessionDescription(
                sdp=answer_sdp_for_device(remote.sdp, answer.sdp),
                type=answer.type,
            ))
        except HTTPException:
            raise
        except (ValueError, AssertionError, KeyError) as exc:
            raise HTTPException(status_code=400, detail="Invalid device offer") from exc
        try:
            session = await kernel.publish_session(pc, tracks[0])
        except SessionStateError as exc:
            raise HTTPException(status_code=503, detail="Capture service is shutting down") from exc

        @pc.on("datachannel")
        def on_datachannel(channel):
            session.attach_channel(channel)
        return pc, session
    except BaseException:
        if session is not None:
            await session.close()
        await pc.close()
        raise


def create_signaling_router(kernel) -> APIRouter:
    """构建挂载在应用顶层的 WHIP 设备入流路由。"""
    router = APIRouter(tags=["采集信令"])
    # 每客户端滑动窗口准入；仅用于设备入流信令。
    limiter = SlidingWindowRateLimiter(WHIP_RATE_LIMIT, WHIP_RATE_WINDOW_SECONDS)

    @router.post("/whip/capture", status_code=201)
    async def whip_post(request: Request, _auth: None = Depends(device_session)):
        client = request.client.host if request.client else "unknown"
        if not limiter.allow(client):
            raise HTTPException(status_code=429, detail="Device signaling rate limit exceeded")
        offer_sdp = read_whip_offer(request, await request.body())
        pc, session = await _publish_device_session(kernel, offer_sdp)
        return Response(
            content=pc.localDescription.sdp,
            status_code=201,
            media_type=SDP_CONTENT_TYPE,
            headers={"Location": f"/whip/capture/{session.id}"},
        )

    @router.delete("/whip/capture/{resource_id}")
    async def whip_delete(resource_id: str, _auth: None = Depends(device_session)):
        if not await kernel.release_session(resource_id):
            raise HTTPException(status_code=404, detail="Unknown WHIP resource")
        return JSONResponse({"released": resource_id})

    return router


__all__ = [
    "SDP_CONTENT_TYPE",
    "WHIP_SDP_MAX_BYTES",
    "answer_sdp_for_device",
    "create_signaling_router",
    "device_session",
    "read_whip_offer",
]
