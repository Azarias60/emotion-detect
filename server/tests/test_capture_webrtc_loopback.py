# -*-coding: utf-8 -*-
"""进程内全链路回环：aiortc 设备模拟器 → WHIP POST /whip/capture → 采集内核产出。

覆盖规格 §6.1 验收：信令握手、三通道挂载、jpeg_run/jpeg_ready 代际确认、
分块 JPEG 重组进内存帧端口、传感器状态快照进摘要端口、单会话排他替换。
"""
import asyncio
import json
import socket
import time
from fractions import Fraction

import pytest
from aioice import ice
from aiortc import RTCPeerConnection, RTCConfiguration, RTCSessionDescription
from aiortc.mediastreams import MediaStreamTrack
from av import AudioFrame, CodecContext
from httpx import ASGITransport, AsyncClient

from server import settings
from server.app import create_app
from server.bootstrap import assemble
from server.capture.webrtc.kernel import create_kernel
from server.tests.contract_stubs import build_jpeg_bytes

CONNECT_TIMEOUT = 15.0
DATA_TIMEOUT = 15.0


class SilenceTrack(MediaStreamTrack):
    """原生 48kHz 单声道 PCM 编码的 20ms Opus 包。"""

    kind = "audio"

    def __init__(self):
        super().__init__()
        self._pts = 0
        self._encoder = CodecContext.create("libopus", "w")
        self._encoder.sample_rate = 48000
        self._encoder.layout = "mono"
        self._encoder.format = "s16"
        self._encoder.bit_rate = 24000
        self._encoder.open()
        assert self._encoder.frame_size == 960

    async def recv(self):
        await asyncio.sleep(0.02)
        frame = AudioFrame(format="s16", layout="mono", samples=960)
        frame.sample_rate = 48000
        frame.pts = self._pts
        frame.time_base = Fraction(1, 48000)
        for plane in frame.planes:
            plane.update(b"\x00\x00" * 960)
        self._pts += 960
        packets = self._encoder.encode(frame)
        assert len(packets) == 1
        return packets[0]


def _chunk(jpeg: bytes, chunk_size: int = 4096, pts_ms: int = 1000):
    chunks = [jpeg[i:i + chunk_size] for i in range(0, len(jpeg), chunk_size)]
    messages = []
    for index, payload in enumerate(chunks):
        flag = index & 0x7F
        if index == len(chunks) - 1:
            flag |= 0x80
        messages.append(bytes([flag]) + len(payload).to_bytes(4, "big") + pts_ms.to_bytes(4, "big") + payload)
    return messages


def _state(bpm=72.5, ambient=25.0, object_temp=36.2, run=1, sampled_at_us=1):
    return json.dumps({
        "v": 1, "type": "state", "enabled": True, "run": run,
        "audio_base_ms": 0,
        "temperature": {
            "valid": True, "sampled_at_us": sampled_at_us,
            "ambient_c": ambient, "object_c": object_temp, "error": None,
        },
        "heart_rate": {
            "valid": True, "sampled_at_us": sampled_at_us,
            "bpm": bpm, "error": None,
        },
    })


async def _wait_for(predicate, timeout, what):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.05)
    raise AssertionError(f"等待超时：{what}")


class DeviceSimulator:
    """按 esp32s3capture 固件行为驱动的对端。"""

    def __init__(self):
        # 空 iceServers：回环两端都靠主机候选直连，不依赖外部 STUN。
        self.pc = RTCPeerConnection(RTCConfiguration(iceServers=[]))
        self.capture_messages = []
        self.jpeg_ack = asyncio.Event()
        self.capture = None
        self.jpeg = None

    async def connect(self, http):
        self.capture = self.pc.createDataChannel("capture")
        self.jpeg = self.pc.createDataChannel(
            "jpeg", ordered=True, maxPacketLifeTime=200)
        self.pc.addTrack(SilenceTrack())

        @self.capture.on("message")
        def on_capture(message):
            self.capture_messages.append(json.loads(message))
            if message and '"jpeg_ready"' in message:
                self.jpeg_ack.set()

        offer = await self.pc.createOffer()
        await self.pc.setLocalDescription(offer)
        response = await http.post(
            "/whip/capture",
            content=self.pc.localDescription.sdp,
            headers={
                "content-type": "application/sdp",
                "Authorization": f"Bearer {TEST_DEVICE_TOKEN}",
            },
        )
        assert response.status_code == 201, response.text
        self.whip_resource = response.headers["Location"]
        assert "a=setup:passive" in response.text
        await self.pc.setRemoteDescription(RTCSessionDescription(
            sdp=response.text, type="answer"))
        await _wait_for(
            lambda: self.pc.connectionState == "connected"
            and self.capture.readyState == "open"
            and self.jpeg.readyState == "open",
            CONNECT_TIMEOUT, "设备 ICE/DTLS/SCTP 连接",
        )

    def send_frame(self, jpeg: bytes):
        self.jpeg.send(json.dumps({"v": 1, "type": "jpeg_run", "run": 1}))
        for message in _chunk(jpeg):
            self.jpeg.send(message)

    def send_state(self, **kwargs):
        self.capture.send(_state(**kwargs))

    async def close(self, http=None, *, expect_release=True):
        if http is not None and getattr(self, "whip_resource", None):
            release = await http.delete(
                self.whip_resource,
                headers={"Authorization": f"Bearer {TEST_DEVICE_TOKEN}"},
            )
            # 被新会话替换的旧资源已失效：按 WHIP 契约返回 404。
            assert release.status_code == (200 if expect_release else 404)
        await self.pc.close()


TEST_DEVICE_TOKEN = "loopback-device-token"


def test_device_loopback_delivers_frames_sensors_and_session_exclusivity(monkeypatch):
    # 真实回环只需要 IPv4 localhost，无需枚举宿主机的网卡。
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.bind(("127.0.0.1", 0))
            probe.sendto(b"probe", probe.getsockname())
    except PermissionError as error:
        pytest.skip(f"UDP loopback is denied: {error}")
    monkeypatch.setattr(ice, "get_host_addresses", lambda **kwargs: ["127.0.0.1"])

    async def main(kernel):
        settings.DEVICE_SIGNAL_TOKEN = TEST_DEVICE_TOKEN
        settings.DEVICE_SIGNAL_TOKEN_GRACE = False
        app = create_app(assemble(kernel=kernel))
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as http:
            device = DeviceSimulator()
            await device.connect(http)

            # 固件行为：会话启动先上报 state（run=1, enabled=true），
            # 之后才发送 JPEG 代际标记与分块图像。
            device.send_state(bpm=72.5, ambient=25.0, object_temp=36.2)
            await asyncio.sleep(0.3)

            # 代际确认：服务端必须在 capture 通道回 jpeg_ready
            device.send_frame(build_jpeg_bytes(size=(320, 240)))
            await asyncio.wait_for(device.jpeg_ack.wait(), DATA_TIMEOUT)
            acks = [m for m in device.capture_messages if m.get("type") == "jpeg_ready"]
            assert acks and acks[0]["run"] == 1

            # 帧进入内存端口（原始 JPEG 字节入库，窗口按需解码）
            await _wait_for(lambda: kernel.video_window() is not None,
                            DATA_TIMEOUT, "内核视频窗口")
            assert next(iter(kernel.video_window())).data.shape[2] == 3
            assert kernel.is_connected() is True

            # 传感器快照进入采样窗口端口
            await _wait_for(
                lambda: (kernel.heart_rate_window() or None) is not None
                and kernel.heart_rate_window().samples_bpm == (72.5,),
                DATA_TIMEOUT, "心率采样")
            temperature = kernel.temperature_window()
            assert temperature.latest_object_temp == 36.2

            # 音频进入环形缓冲（Opus 解码 → 单声道 s16）
            await _wait_for(lambda: len(kernel.audio_buffer) > 0,
                            DATA_TIMEOUT, "音频缓冲")
            assert kernel.audio_window() is not None

            # 单会话排他：新 Offer 到达后旧会话被完整回收
            first_session = kernel.hub.current
            replacement = DeviceSimulator()
            await replacement.connect(http)
            assert kernel.hub.current is not first_session
            await _wait_for(lambda: first_session.closed, DATA_TIMEOUT, "旧会话关闭")
            replacement.send_state(bpm=80.0)
            await _wait_for(
                lambda: kernel.heart_rate_window() is not None
                and kernel.heart_rate_window().samples_bpm == (80.0,),
                DATA_TIMEOUT, "新会话心率（旧证据被隔离）")

            await replacement.close(http)
            await device.close(http, expect_release=False)
            await kernel.close()

    asyncio.run(main(create_kernel()))
