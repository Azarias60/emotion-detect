# -*-coding: utf-8 -*-
"""esp_webrtc 分块 JPEG 通道的重组与解码。

分块报文（固件 ``esp_webrtc.c send_video_over_data_channel``）：
- 字节 0：低 7 位为分块序号（每帧从 0 重新计数），``0x80`` 为结束位；
- 字节 1-4：负载长度（大端 uint32）；
- 字节 5-8：设备拍摄时刻（大端 uint32，毫秒）。
- 字节 9..：JPEG 数据分片。

重组规则：序号 0 总是开始新帧（丢包后立即恢复）；乱序或超限即丢弃残帧；
单块 ≤4096B、≤64 块、1 秒重组超时。解码由 ``OpenCvCanonicalFrameDecoder``
按需执行：分辨率不设上限，画面尺寸以解码结果为准，安装方向归一化在解码
的同一边界统一应用。
"""

import time

import cv2
import numpy as np

from server.logutil import get_logger

logger = get_logger(__name__)

MAX_CHUNK_BYTES = 4096
MAX_CHUNKS = 64
FRAME_TIMEOUT_SECONDS = 1.0
HEADER_BYTES = 9


class JpegReassembler:
    """按序重组 JPEG 并展开设备 32 位毫秒时钟。"""

    def __init__(self, *, clock=time.monotonic):
        self._clock = clock
        self.discarded = 0
        self._parts: list[bytes] = []
        self._next_sequence: int | None = None
        self._started_at: float | None = None
        self._frame_pts: int | None = None
        self._last_pts: int | None = None
        self._capture_ms = 0
        self._clock_rolled_back = False

    def _clear(self):
        self._parts = []
        self._next_sequence = None
        self._started_at = None
        self._frame_pts = None

    def _discard(self):
        self.discarded += 1
        self._clear()

    def _expire(self, now):
        if self._started_at is not None and now - self._started_at >= FRAME_TIMEOUT_SECONDS:
            self._discard()

    def feed(self, message) -> tuple[bytes, float] | None:
        if self._clock_rolled_back:
            self.discarded += 1
            return None
        now = self._clock()
        self._expire(now)

        if not isinstance(message, (bytes, bytearray, memoryview)):
            self._discard()
            return None
        message = bytes(message)
        if len(message) < HEADER_BYTES:
            self._discard()
            return None

        flag = message[0]
        sequence = flag & 0x7F
        is_final = bool(flag & 0x80)
        payload_length = int.from_bytes(message[1:5], "big")
        pts = int.from_bytes(message[5:9], "big")
        payload = message[HEADER_BYTES:]
        if (
            payload_length == 0
            or payload_length > MAX_CHUNK_BYTES
            or payload_length != len(payload)
        ):
            self._discard()
            return None
        if sequence >= MAX_CHUNKS:
            self._discard()
            return None

        if sequence == 0:
            if self._started_at is not None:
                self._discard()  # 上一帧残片被新帧序号 0 抢占。
            self._started_at = now
            self._next_sequence = 1
            self._frame_pts = pts
        elif sequence != self._next_sequence or pts != self._frame_pts:
            self._discard()
            return None

        self._parts.append(payload)
        self._next_sequence = (sequence + 1) & 0x7F

        if is_final:
            frame = b"".join(self._parts)
            self._clear()
            delta = pts if self._last_pts is None else (pts - self._last_pts) & 0xFFFFFFFF
            if self._last_pts is not None and delta == 0:
                self.discarded += 1
                return None
            if self._last_pts is not None and delta >= 0x80000000:
                # The JPEG channel is ordered. A backwards clock cannot catch up
                # and resume this timeline. The capture fence creates a new parser.
                self._clock_rolled_back = True
                self.discarded += 1
                logger.warning("JPEG 设备采集时钟回退，停止发布当前代际的视频帧")
                return None
            self._last_pts = pts
            self._capture_ms += delta
            return frame, self._capture_ms / 1000
        return None


class OpenCvCanonicalFrameDecoder:
    """全仓唯一的规范帧解码器（依赖注入给 VideoWindow 与预览视频轨）。

    JPEG 解码用 OpenCV（无分辨率上限），随后以反对角线无损转置
    （``cv2.flip(cv2.transpose(frame), -1)``，等价于逆时针旋转 90 度后
    水平镜像）把设备安装方向归一化为应用坐标，天然产生连续 C 内存。
    坏帧返回 ``None``，由调用方跳过。
    """

    def decode(self, jpeg: bytes) -> np.ndarray | None:
        if not jpeg:
            return None
        image = cv2.imdecode(np.frombuffer(jpeg, dtype=np.uint8), cv2.IMREAD_COLOR)
        if image is None:
            return None
        return cv2.flip(cv2.transpose(image), -1)


__all__ = [
    "JpegReassembler",
    "MAX_CHUNK_BYTES",
    "OpenCvCanonicalFrameDecoder",
]
