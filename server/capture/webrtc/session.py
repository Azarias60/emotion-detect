# -*-coding: utf-8 -*-
"""采集会话与会话中枢：连接生命周期状态机的实现层。

单会话排他（规格 §5）：新 Offer 到达时先完整回收旧 ``RTCPeerConnection``
（音频排空、通道关闭、看门狗终止），再发布新会话，防止句柄与任务泄漏。

通道契约（v1）：
- ``jpeg``（有序可靠）：二进制分块 JPEG + 文本 ``jpeg_run``
  代际标记；服务端必须在 ``capture`` 通道回 ``jpeg_ready`` 确认；
- ``capture``（可靠有序，JSON 文本）：500ms ``state`` 快照（传感器 +
  enabled/run/audio_base_ms）。run 代际变化时清空媒体发布状态。
"""

import asyncio
import json
import logging
import time
import uuid

from server.capture.webrtc.audio import AudioReceiver
from server.capture.webrtc.jpeg import JpegReassembler
from server.capture.webrtc.protocol import (
    JpegRunProtocolError,
    parse_capture_message,
    parse_jpeg_run_marker,
    run_is_older,
)

logger = logging.getLogger("physiosense.capture")

VIDEO_STALL_SECONDS = 5.0
VIDEO_WATCH_POLL_SECONDS = 1.0


class SessionStateError(Exception):
    """会话已结束或通道不可用。"""


class _JpegChannel:
    """单通道 JPEG 摄入；按 run 代际栅栏发布原始字节，不做任何解码。"""

    def __init__(self, kernel, *, on_channel_closed):
        self._kernel = kernel
        self._on_channel_closed = on_channel_closed
        self.channel = None
        self._parser = JpegReassembler()
        self._generation = 0
        self._enabled = True
        self._active_run: int | None = None
        self._fenced_run: int | None = None
        self.closed = False

    def _can_publish(self):
        return self._enabled and (
            self._active_run is None or self._fenced_run == self._active_run
        )

    def _invalidate(self):
        self._generation += 1
        # 代际栅栏失效即丢弃重组残帧：新代次从全新重组状态开始。
        self._parser = JpegReassembler()

    def require_run_fence(self):
        """控制通道出现后，要求先观察到 run 标记才发布媒体。"""
        if self.closed:
            return False
        if self._active_run is not None:
            return True
        self._active_run = 0
        self._fenced_run = None
        self._invalidate()
        return True

    def set_run(self, run) -> bool:
        """以控制通道的权威 run 更新；不覆盖已观察到的栅栏。"""
        if self.closed or (self._active_run is not None and run_is_older(run, self._active_run)):
            return False
        if self._active_run == run:
            return True
        self._active_run = run
        self._invalidate()
        return True

    def mark_run(self, run) -> bool:
        """记录 JPEG 通道代际边界，作废此前的一切媒体工作。"""
        if (self.closed or self._active_run is None or run_is_older(run, self._active_run)
                or (self._fenced_run is not None and run_is_older(run, self._fenced_run))):
            return False
        if self._fenced_run == run:
            return True
        self._fenced_run = run
        self._invalidate()
        return True

    @property
    def fenced_run(self):
        return self._fenced_run

    def set_enabled(self, enabled: bool):
        if self.closed or type(enabled) is not bool or self._enabled == enabled:
            return
        self._enabled = enabled
        self._invalidate()

    def attach(self, channel, *, on_run_marker=None) -> bool:
        if self.closed or channel.label != "jpeg" or channel.ordered is not True:
            channel.close()
            return False
        if self.channel is channel:
            return True
        previous = self.channel
        self._invalidate()
        if previous is not None and self._active_run is not None:
            self._fenced_run = None
        self.channel = channel

        @channel.on("message")
        def on_message(message):
            if self.closed or self.channel is not channel:
                return
            if isinstance(message, str):
                if on_run_marker is not None:
                    on_run_marker(channel, message)
                return
            if not self._can_publish():
                return
            frame = self._parser.feed(message)
            if frame is None:
                return
            image, captured_at = frame
            # 廉价结构判据：SOI/EOI 定界符非法即坏帧，直接丢弃；
            # 合法帧发布原始字节入库，摄入 CPU 开销近乎为零。
            if not (image[:2] == b"\xff\xd8" and image[-2:] == b"\xff\xd9"):
                return
            self._kernel.publish_frame(image, time.monotonic(), captured_at)

        @channel.on("close")
        def on_close():
            if self.channel is channel:
                self.channel = None
                self._invalidate()
                if self._on_channel_closed is not None and not self.closed:
                    self._on_channel_closed(channel)

        if previous is not None:
            previous.close()
        return True

    async def close(self):
        self.closed = True
        self._enabled = False
        self._generation += 1
        if self.channel is not None:
            self.channel.close()
            self.channel = None


class CaptureSession:
    """一次设备 WebRTC 连接持有的全部接收器与控制状态。"""

    def __init__(self, hub, kernel, peer, track):
        self.id = uuid.uuid4().hex
        self.hub = hub
        self.kernel = kernel
        self.peer = peer
        self.closed = False
        self._close_task: asyncio.Task | None = None
        self.jpeg = _JpegChannel(kernel, on_channel_closed=self._jpeg_channel_closed)
        self.audio = AudioReceiver(track, kernel.audio_buffer, self._audio_ended)
        self._capture_channel = None
        self._capture_channel_epoch = 0
        self._capture_enabled = True  # 未出现控制通道时保持流式（legacy 语义）
        self._capture_run = 0
        self._audio_base_ms: int | None = None
        self._transitioning = False
        self._video_start_time: float | None = None
        self._message_lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # 异常路径
    # ------------------------------------------------------------------
    def _audio_ended(self):
        if not self.closed:
            self.hub.spawn(self.close())

    def _jpeg_channel_closed(self, channel):
        if not self.closed and self.jpeg.channel is None:
            logger.info("会话 %s 的 JPEG 通道异常关闭", self.id)
            self.hub.spawn(self.close())

    # ------------------------------------------------------------------
    # 媒体发布门控
    # ------------------------------------------------------------------
    def _begin_transition(self):
        self._transitioning = True
        self._capture_enabled = False
        self._video_start_time = None
        self.audio.set_enabled(False)
        self.jpeg.set_enabled(False)

    def _finish_state(self, enabled):
        self._capture_enabled = enabled
        self._transitioning = False
        self.audio.set_enabled(enabled)
        self.jpeg.set_run(self._capture_run)
        self.jpeg.set_enabled(enabled)
        self._start_video_deadline_if_ready()

    def _start_video_deadline_if_ready(self):
        if (self._video_start_time is None and not self.closed and not self._transitioning
                and self._capture_enabled and self._capture_channel is not None
                and self.jpeg.fenced_run == self._capture_run):
            self._video_start_time = time.monotonic()

    # ------------------------------------------------------------------
    # 通道接入
    # ------------------------------------------------------------------
    def attach_channel(self, channel) -> bool:
        if self.closed:
            channel.close()
            return False
        if channel.label == "jpeg":
            if (self._capture_channel is not None and self.jpeg.channel is not None
                    and channel is not self.jpeg.channel):
                channel.close()
                return False
            return self.jpeg.attach(channel, on_run_marker=self._handle_jpeg_run)
        if channel.label != "capture":
            channel.close()
            return False
        if (getattr(channel, "ordered", True) is not True
                or getattr(channel, "maxRetransmits", None) is not None
                or getattr(channel, "maxPacketLifeTime", None) is not None):
            channel.close()
            return False
        if self._capture_channel is not None:
            channel.close()
            return False

        self._capture_channel = channel
        self._capture_channel_epoch += 1
        channel_epoch = self._capture_channel_epoch
        self._capture_enabled = False
        self._capture_run = 0
        self.jpeg.require_run_fence()
        self._begin_transition()

        @channel.on("message")
        def on_message(message):
            if (self._capture_channel is channel
                    and channel_epoch == self._capture_channel_epoch):
                self.hub.spawn(self._handle_capture_message(channel, channel_epoch, message))

        @channel.on("close")
        def on_close():
            if (self._capture_channel is channel
                    and channel_epoch == self._capture_channel_epoch):
                self.hub.spawn(self.close())

        return True

    def _handle_jpeg_run(self, channel, payload) -> bool:
        """确认一次 JPEG 代际边界，并在控制通道回 ``jpeg_ready``。"""
        capture_channel = self._capture_channel
        if (self.closed or channel is not self.jpeg.channel or capture_channel is None
                or getattr(capture_channel, "readyState", None) != "open"):
            return False
        try:
            run = parse_jpeg_run_marker(payload)
        except JpegRunProtocolError as exc:
            logger.debug("忽略会话 %s 的非法 JPEG 代际标记：%s", self.id, exc)
            return False
        if not self.jpeg.mark_run(run):
            return False
        try:
            capture_channel.send(json.dumps(
                {"v": 1, "type": "jpeg_ready", "run": run}, separators=(",", ":")))
        except Exception as exc:
            logger.warning("会话 %s 确认 JPEG 代际 %s 失败：%s", self.id, run, exc)
            return False
        self._start_video_deadline_if_ready()
        return True

    async def _handle_capture_message(self, channel, channel_epoch, payload):
        if (self.closed or channel is not self._capture_channel
                or channel_epoch != self._capture_channel_epoch):
            return
        async with self._message_lock:
            if (self.closed or channel is not self._capture_channel
                    or channel_epoch != self._capture_channel_epoch):
                return
            try:
                message = parse_capture_message(payload)
            except ValueError as exc:
                logger.debug("忽略会话 %s 的非法控制消息：%s", self.id, exc)
                return
            if message["type"] == "state":
                await self._handle_state(message)

    async def _handle_state(self, message):
        run = message["run"]
        if run_is_older(run, self._capture_run):
            return
        audio_base_ms = message["audio_base_ms"]
        if (run == self._capture_run and self._audio_base_ms is not None
                and audio_base_ms != self._audio_base_ms):
            return
        run_changed = run != self._capture_run
        enabled_changed = message["enabled"] != self._capture_enabled
        if run_changed or enabled_changed:
            self._begin_transition()
            if self.closed:
                return
        if not self.jpeg.set_run(run):
            return
        if run_changed:
            self.kernel.clear_media_state()
            self._capture_run = run
        self._audio_base_ms = audio_base_ms
        self.audio.set_audio_base_ms(audio_base_ms)
        self.kernel.update_sensors(message)
        self._finish_state(message["enabled"])

    # ------------------------------------------------------------------
    # 状态与生命周期
    # ------------------------------------------------------------------
    def video_stalled_for(self):
        """已启用采集但 JPEG 流停止更新的秒数；不适用返回 None。"""
        if (self.closed or self._transitioning or not self._capture_enabled
                or self.peer.connectionState != "connected"):
            return None
        age = self.kernel.last_frame_age()
        if age is None:
            if self._video_start_time is None:
                return None
            return time.monotonic() - self._video_start_time
        return age

    async def close(self):
        if self._close_task is None:
            self.closed = True
            self._begin_transition()
            self._close_task = asyncio.create_task(self._close())
        await asyncio.shield(self._close_task)

    async def _close(self):
        await self.audio.close()
        await self.jpeg.close()
        await self.peer.close()
        if self.hub.current is self:
            self.hub.current = None
        self.kernel.on_session_closed(self)


class CaptureHub:
    """选择完全协商成功的会话，并原子地整体替换旧会话的媒体。"""

    def __init__(self, kernel, *, stall_seconds=VIDEO_STALL_SECONDS,
                 poll_seconds=VIDEO_WATCH_POLL_SECONDS):
        self.kernel = kernel
        self.current: CaptureSession | None = None
        self._closing = False
        self._switch = asyncio.Lock()
        self._tasks: set[asyncio.Task] = set()
        self._stall_seconds = stall_seconds
        self._poll_seconds = poll_seconds

    def spawn(self, coroutine):
        task = asyncio.create_task(coroutine)
        self._tasks.add(task)

        def finished(done):
            self._tasks.discard(done)
            if not done.cancelled() and done.exception():
                logger.error("采集清理任务失败：%s", done.exception())

        task.add_done_callback(finished)

    async def _watch_video(self, session):
        """限制上游视频停更：要么有新帧，要么重建连接。"""
        while not session.closed and self.current is session:
            await asyncio.sleep(self._poll_seconds)
            if session.closed or self.current is not session:
                return
            stalled = session.video_stalled_for()
            if stalled is not None and stalled >= self._stall_seconds:
                logger.warning(
                    "会话 %s 的 JPEG 流停更 %.1fs，回收连接等待设备重连",
                    session.id, stalled,
                )
                await session.close()
                return

    async def publish(self, peer, track) -> CaptureSession:
        async with self._switch:
            if self._closing:
                raise SessionStateError("采集服务正在关闭")
            if self.current is not None:
                await self.current.close()
            if self._closing:
                raise SessionStateError("采集服务正在关闭")
            session = CaptureSession(self, self.kernel, peer, track)
            self.kernel.on_session_published(session)
            self.current = session
            self.spawn(self._watch_video(session))
            return session

    async def close(self):
        self._closing = True
        async with self._switch:
            if self.current is not None:
                await self.current.close()
        if self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)


__all__ = ["CaptureHub", "CaptureSession", "SessionStateError"]
