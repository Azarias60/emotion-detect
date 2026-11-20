# -*-coding: utf-8 -*-
"""设备 Opus 音频轨的接收、解码与环形缓冲。

设备将 48kHz 单声道 PCM 直接编码为 Opus（24kbps、20ms 帧）。接收后
交给 capture 音频处理器统一为 48kHz 单声道 PCM16，再执行降噪和 AGC；
缓冲与统计使用处理后的实际样本。ASR 在文件消费边界请求 16kHz WAV。

PTS 下限过滤（``audio_base_ms``）：设备重启采集后时间戳回退，按当前代际
的保守 PTS 下限拒绝旧帧，避免旧音频混入新代际（固件 ``audio_base_ms``
语义，见 esp32s3capture docs/step-07）。5 秒无帧看门狗结束会话，由设备
退避重连。
"""

import asyncio
import time

from aiortc.mediastreams import MediaStreamError
from server import settings
from server.capture.webrtc.audio_processing import (
    AUDIO_CHANNELS, AUDIO_SAMPLE_RATE, AUDIO_SAMPLE_WIDTH, CaptureAudioProcessor,
)

AUDIO_WINDOW_SECONDS = settings.CAPTURE_WINDOW_SECONDS


class AudioBuffer:
    """线程安全的单声道 s16 PCM 有界缓冲（按字节数截断最旧数据）。

    同时记录最近一次 ``extend`` 的单调时钟时刻（``last_arrival``）与缓冲
    当前容纳的音频时长（``duration_seconds``），供状态包络把显示窗口对
    齐到设备真实到达的音频，而不是按固定窗口左补静音。
    """

    MAX_BYTES = AUDIO_SAMPLE_RATE * AUDIO_SAMPLE_WIDTH * int(AUDIO_WINDOW_SECONDS)

    def __init__(self, on_append=None):
        self._on_append = on_append
        self._data = bytearray()
        self._last_arrival: float | None = None

    def extend(self, pcm: bytes, *, clock=time.monotonic) -> None:
        if not pcm:
            return
        self._data.extend(pcm)
        excess = len(self._data) - self.MAX_BYTES
        if excess > 0:
            del self._data[:excess]
        self._last_arrival = clock()
        if self._on_append:
            self._on_append(pcm)

    def snapshot(self) -> bytes:
        return bytes(self._data)

    def clear(self) -> None:
        self._data.clear()
        self._last_arrival = None

    def __len__(self) -> int:
        return len(self._data)

    @property
    def last_arrival(self) -> float | None:
        return self._last_arrival

    @property
    def duration_seconds(self) -> float:
        return len(self._data) / (AUDIO_SAMPLE_RATE * AUDIO_SAMPLE_WIDTH)


class AudioReceiver:
    """排空设备音频轨，过滤旧 PTS 帧并写入环形缓冲。"""

    WATCHDOG_SECONDS = 5.0

    def __init__(self, track, sink: AudioBuffer, on_ended, *, clock=time.monotonic):
        self.track = track
        self._sink = sink
        self._on_ended = on_ended
        self._clock = clock
        self._audio_base_ms: int | None = None
        self.enabled = True
        self._processor = CaptureAudioProcessor()
        self._closing = False
        self._watchdog_generation = 0
        self._watchdog_deadline = asyncio.get_running_loop().time() + self.WATCHDOG_SECONDS
        self._watchdog_changed = asyncio.Event()
        self._monitor = asyncio.create_task(self._receive())

    def set_audio_base_ms(self, audio_base_ms):
        """设置当前设备代际的保守解码 PTS 下限（毫秒）。"""
        if (audio_base_ms is not None
                and (type(audio_base_ms) is not int or audio_base_ms < 0)):
            raise ValueError("audio_base_ms 必须是非负整数或 None")
        if self._audio_base_ms == audio_base_ms:
            return
        self._audio_base_ms = audio_base_ms
        self._reset_processor()
        if self.enabled:
            self._watchdog_deadline = asyncio.get_running_loop().time() + self.WATCHDOG_SECONDS
            self._watchdog_generation += 1
            self._watchdog_changed.set()

    def accepts_frame(self, frame) -> bool:
        base = self._audio_base_ms
        if base is None:
            return True
        if frame.pts is None or frame.time_base is None:
            return False
        try:
            pts_ms = float(frame.pts) * float(frame.time_base) * 1000.0
        except OverflowError:
            return False
        import math

        return math.isfinite(pts_ms) and pts_ms >= base

    def set_enabled(self, enabled: bool) -> None:
        """暂停/恢复统计与看门狗；排空循环始终继续以防止 RTP 积压。"""
        if self._closing or type(enabled) is not bool or self.enabled == enabled:
            return
        self.enabled = enabled
        self._reset_processor()
        self._watchdog_generation += 1
        if enabled:
            self._watchdog_deadline = asyncio.get_running_loop().time() + self.WATCHDOG_SECONDS
        self._watchdog_changed.set()

    def _reset_processor(self):
        # Neither resampling, denoising nor gain history may cross a capture run.
        if self._processor is not None:
            self._processor.close()
        self._processor = CaptureAudioProcessor() if self.enabled else None

    def _append_pcm(self, pcm):
        self._sink.extend(pcm)

    def _record_frame(self, frame):
        self._watchdog_deadline = asyncio.get_running_loop().time() + self.WATCHDOG_SECONDS
        self._append_pcm(self._processor.process(frame))

    async def _receive(self):
        recv_task = asyncio.create_task(self.track.recv())
        try:
            while True:
                self._watchdog_changed.clear()
                generation = self._watchdog_generation
                timeout = None
                if self.enabled:
                    timeout = max(
                        0.0,
                        self._watchdog_deadline - asyncio.get_running_loop().time(),
                    )
                changed_task = asyncio.create_task(self._watchdog_changed.wait())
                try:
                    done, _ = await asyncio.wait(
                        (recv_task, changed_task), timeout=timeout,
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                finally:
                    if not changed_task.done():
                        changed_task.cancel()
                    await asyncio.gather(changed_task, return_exceptions=True)

                if (self.enabled and generation == self._watchdog_generation
                        and asyncio.get_running_loop().time() >= self._watchdog_deadline):
                    raise asyncio.TimeoutError
                if recv_task in done:
                    frame = recv_task.result()
                    recv_task = asyncio.create_task(self.track.recv())
                    if self.enabled and self.accepts_frame(frame):
                        self._record_frame(frame)
                    continue
                if changed_task in done:
                    continue
        except MediaStreamError:
            if self.enabled:
                self._append_pcm(self._processor.finish())
        except (TimeoutError, asyncio.CancelledError):
            pass
        finally:
            if self._processor is not None:
                self._processor.close()
                self._processor = None
            if not recv_task.done():
                recv_task.cancel()
            await asyncio.gather(recv_task, return_exceptions=True)
            if not self._closing:
                self._on_ended()

    async def close(self):
        self.set_enabled(False)
        self._closing = True
        self._watchdog_changed.set()
        self._monitor.cancel()
        await asyncio.gather(self._monitor, return_exceptions=True)
        self.track.stop()


__all__ = ["AUDIO_SAMPLE_RATE", "AUDIO_CHANNELS", "AUDIO_SAMPLE_WIDTH", "AudioBuffer", "AudioReceiver"]
