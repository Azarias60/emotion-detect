# -*-coding: utf-8 -*-
"""采集驱动内核门面：四大模态统一的时序切片服务（规格 §3.3）。

业务层仅通过 ``CapturePort`` 读取内存数据（亚毫秒级、不阻塞推理循环），
调用方需要几秒就基于单调时间戳切出最后几秒，无数据统一返回 ``None``：
- ``video_window(seconds)``：最后 seconds 秒的帧日志快照（不传给全量）；
- ``audio_window(seconds, sample_rate)``：同源音频缓冲的字节截断快照；
- ``heart_rate_window(seconds)`` / ``temperature_window(seconds)``：传感器
  采样队列的时间切片；
- ``is_connected()``：当前设备 WebRTC 连接是否健康在线。

内核自身不包含业务决策，也不做任何前端 UI 视觉计算；写入口
（``publish_frame`` / ``update_sensors``）只被采集会话（事件循环侧）调用，
原始 JPEG 字节直接入库，解码完全推迟到消费者按需执行。读方法可从任意
线程安全调用。新会话发布时整体清空上一会话的证据，保证代际隔离。
"""

import asyncio
import threading
import time
from collections import deque

from server import settings
from server.capture.domain import (
    AudioWindow, HeartRateWindow, TemperatureWindow, VideoWindow,
)
from server.capture.webrtc.audio import AUDIO_SAMPLE_RATE, AUDIO_SAMPLE_WIDTH, AudioBuffer
from server.capture.webrtc.jpeg import OpenCvCanonicalFrameDecoder
from server.capture.webrtc.sensors import SensorState
from server.capture.webrtc.recording import Recording
from server.detection_errors import DetectionBusyError, DetectionInputError

FRAME_FRESH_SECONDS = 2.0


class VideoFrameSubscription:
    """有界最新帧订阅；慢订阅者跳过旧帧，不创建无界队列。

    ``next_frame`` 返回 ``(jpeg_bytes, captured_at, sequence)``：最新到达且
    重组完整的原始 JPEG 字节，解码由订阅方自行按需执行。
    """

    def __init__(self, publisher, loop):
        self._publisher = publisher
        self._loop = loop
        self._event = asyncio.Event()
        self._sequence = 0
        self._closed = False

    async def next_frame(self, timeout: float):
        while not self._closed:
            with self._publisher._lock:
                latest = self._publisher._fresh_video_locked()
                if latest is not None and self._publisher._frame_sequence > self._sequence:
                    self._sequence = self._publisher._frame_sequence
                    _, jpeg, captured_at = latest
                    return jpeg, captured_at, self._sequence
                # 生产者推进序号持同一把锁：锁内读完即清事件，两步之间不可能
                # 插入推进，无需（也不得）二次复查——换代后 journal 已空而旧序号
                # 若未归零，二次复查只会原地空转。
                self._event.clear()
            try:
                await asyncio.wait_for(self._event.wait(), timeout=timeout)
            except asyncio.TimeoutError:
                return None
        return None

    def notify(self):
        if self._closed:
            return
        try:
            self._loop.call_soon_threadsafe(self._event.set)
        except RuntimeError:
            self._closed = True

    def close(self):
        if self._closed:
            return
        self._closed = True
        self._publisher._remove_subscription(self)
        self._event.set()


class CaptureKernel:
    """单设备采集内核：持有会话中枢与跨会话读取状态。"""

    def __init__(self, *, clock=time.monotonic):
        self._clock = clock
        self._lock = threading.Lock()
        self._recording = None
        self.audio_buffer = AudioBuffer(self._record_audio)
        self._sensors = SensorState(clock=clock)
        self._journal: deque[tuple[float, bytes, float]] = deque()
        self._frame_sequence = 0
        self._subscriptions: set[VideoFrameSubscription] = set()
        self._decoder = OpenCvCanonicalFrameDecoder()
        self._closed = False
        from server.capture.webrtc.session import CaptureHub

        self.hub = CaptureHub(self)

    # ------------------------------------------------------------------
    # 会话写入口（事件循环侧调用）
    # ------------------------------------------------------------------
    def publish_frame(self, jpeg: bytes, received_at: float, captured_at: float) -> None:
        with self._lock:
            cutoff = received_at - settings.CAPTURE_WINDOW_SECONDS
            while self._journal and self._journal[0][0] < cutoff:
                self._journal.popleft()
            self._journal.append((received_at, jpeg, captured_at))
            if self._recording and self._recording.receiving(received_at):
                self._recording.frames.append((received_at, jpeg, captured_at))
            self._frame_sequence += 1
            subscribers = tuple(self._subscriptions)
        for subscriber in subscribers:
            subscriber.notify()

    def begin_recording(self):
        with self._lock:
            if not self.is_connected():
                raise DetectionInputError("采集设备离线")
            if self._recording and self._recording.receiving(self._clock()):
                raise DetectionBusyError("设备正在进行另一次检测")
            self._recording = Recording(
                self._clock, self._sensors, self._video_window, self._resample,
                release=self.release_recording, connection_state=self.is_connected,
            )
            return self._recording

    def current_recording(self):
        """当前轮次记录（含已封存、未释放的窗口）；无轮次返回 None。"""
        with self._lock:
            return self._recording

    def release_recording(self, recording):
        with self._lock:
            if self._recording is recording:
                self._recording = None

    def _record_audio(self, pcm):
        with self._lock:
            now = self._clock()
            if self._recording and self._recording.receiving(now):
                self._recording.audio.append((now, pcm))

    def update_sensors(self, state_message: dict) -> None:
        with self._lock:
            self._sensors.accept_state(state_message)
            if self._recording and self._recording.receiving(self._clock()):
                self._recording.sensors.accept_state(state_message)

    def on_session_published(self, session) -> None:
        """新会话整体替换旧会话：清空旧证据，隔离代际。"""
        self.clear_media_state()

    def clear_media_state(self) -> None:
        """清空帧日志、音频缓冲与传感器窗口，帧序号随代次归零，并终止所有帧订阅。

        会话代际隔离的统一入口：旧代际订阅立即失效（``next_frame`` 返回
        ``None``），新代际数据只能被替换后创建的订阅读取；序号同代内单调，
        不跨代延续。
        """
        with self._lock:
            if self._recording and self._recording.receiving(self._clock()):
                self._recording.invalid = True
            self._sensors.reset(0)
            self.audio_buffer.clear()
            self._journal.clear()
            self._frame_sequence = 0
            subscribers = tuple(self._subscriptions)
            self._subscriptions.clear()
        for subscriber in subscribers:
            subscriber.close()

    def on_session_closed(self, session) -> None:
        # 读取统一走时序切片（以到达时间淘汰）；is_connected 由中枢状态决定。
        return

    # ------------------------------------------------------------------
    # 设备会话公开入口（presentation/signaling 调用）
    # ------------------------------------------------------------------
    async def publish_session(self, peer_connection, track):
        """协商完成后发布设备会话；返回持有通道挂接口的会话对象。"""
        return await self.hub.publish(peer_connection, track)

    async def release_session(self, resource_id: str) -> bool:
        """按 WHIP 资源 ID 释放当前会话；未知资源返回 False。"""
        session = self.hub.current
        if session is None or session.id != resource_id:
            return False
        await session.close()
        return True

    # ------------------------------------------------------------------
    # 领域读端口（任意线程）：四大模态对称的时序切片
    # ------------------------------------------------------------------
    def is_connected(self) -> bool:
        session = self.hub.current
        return (session is not None and not session.closed
                and session.peer.connectionState == "connected")

    def last_frame_age(self) -> float | None:
        with self._lock:
            if not self._journal:
                return None
            return max(0.0, self._clock() - self._journal[-1][0])

    def subscribe_video(self) -> VideoFrameSubscription:
        """创建一个只读最新帧订阅，必须由运行中的 asyncio 任务调用。"""
        if self._closed:
            raise RuntimeError("采集内核已关闭")
        loop = asyncio.get_running_loop()
        subscription = VideoFrameSubscription(self, loop)
        with self._lock:
            if self._closed:
                raise RuntimeError("采集内核已关闭")
            self._subscriptions.add(subscription)
        return subscription

    def _remove_subscription(self, subscription: VideoFrameSubscription):
        with self._lock:
            self._subscriptions.discard(subscription)

    def _fresh_video_locked(self):
        """Return the latest live frame; callers hold the journal lock.

        Freshness is independent of the requested tail length and does not
        apply to already captured windows or sealed recordings.
        """
        if not self._journal:
            return None
        latest = self._journal[-1]
        return latest if self._clock() - latest[0] < FRAME_FRESH_SECONDS else None

    def video_window(self, seconds: float | None = None) -> VideoWindow | None:
        """实时帧日志尾部快照；最新帧满 2 秒未更新则返回 None。

        快照在锁内取日志引用；后续新帧、清空
        或代际替换不影响已返回的窗口。seconds 只控制尾部长度，不能延长
        最新帧的新鲜度；封存录制经 _video_window 独立读取。
        """
        with self._lock:
            if self._fresh_video_locked() is None:
                return None
            frames = tuple(self._journal)
        if not frames:
            return None
        if seconds is not None:
            if not seconds > 0:
                raise ValueError("seconds 必须为正数")
            cutoff = frames[-1][2] - seconds
            frames = tuple(item for item in frames if item[2] >= cutoff)
            if not frames:
                return None
        return self._video_window(frames)

    def _video_window(self, frames):
        if not frames:
            return None
        return VideoWindow(frames, self._decoder)

    def audio_window(self, seconds: float | None = None, *,
                     sample_rate: int = AUDIO_SAMPLE_RATE) -> AudioWindow | None:
        """最后 ``seconds`` 秒的音频窗口（右端对齐最新到达）；无数据返回 None。

        ``sample_rate`` 由消费者显式声明（ASR 为 16kHz；默认 48kHz 原样
        直通）。重采样实现与旧 ``audio_wav`` 相同（PyAV s16 单声道）。
        """
        with self._lock:
            tail = self.audio_buffer.snapshot()
            last_arrival = self.audio_buffer.last_arrival
        if seconds is not None:
            if not seconds > 0:
                raise ValueError("seconds 必须为正数")
            max_bytes = int(AUDIO_SAMPLE_RATE * AUDIO_SAMPLE_WIDTH * seconds)
            tail = tail[-max_bytes:]
        if not tail:
            return None
        return AudioWindow(
            pcm=self._resample(tail, sample_rate),
            sample_rate=sample_rate,
            received_at=last_arrival,
        )

    @staticmethod
    def _resample(pcm: bytes, sample_rate: int) -> bytes:
        """48kHz 单声道 s16 → 目标采样率（PyAV，与旧 ``audio_wav`` 同实现）。"""
        if sample_rate == AUDIO_SAMPLE_RATE:
            return pcm
        from av import AudioFrame, AudioResampler
        import numpy as np

        frame = AudioFrame.from_ndarray(
            np.frombuffer(pcm, dtype="<i2").astype(np.int16, copy=False).reshape(1, -1),
            format="s16", layout="mono",
        )
        frame.sample_rate = AUDIO_SAMPLE_RATE
        resampler = AudioResampler(format="s16", layout="mono", rate=sample_rate)
        frames = resampler.resample(frame) + resampler.resample(None)
        return b"".join(item.to_ndarray().astype("<i2", copy=False).tobytes() for item in frames)

    def heart_rate_window(self, seconds: float | None = None) -> HeartRateWindow | None:
        """最后 ``seconds`` 秒的心率采样窗口；无采样返回 None。"""
        with self._lock:
            return self._sensors.heart_rate_window(seconds)

    def temperature_window(self, seconds: float | None = None) -> TemperatureWindow | None:
        """最后 ``seconds`` 秒的体温采样窗口；无采样返回 None。"""
        with self._lock:
            return self._sensors.temperature_window(seconds)

    async def close(self):
        self._closed = True
        with self._lock:
            subscribers = tuple(self._subscriptions)
            self._subscriptions.clear()
        for subscriber in subscribers:
            subscriber.close()
        await self.hub.close()


def create_kernel() -> CaptureKernel:
    """装配采集内核（信令路由与会话中枢共用同一实例）。"""
    return CaptureKernel()


__all__ = ["CaptureKernel", "VideoFrameSubscription", "create_kernel"]
