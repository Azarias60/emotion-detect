"""采集内核的公开读取端口（网关与综合分析经此消费时序切片窗口）。"""

from typing import Protocol

from server.capture.domain import (
    AudioWindow, HeartRateWindow, TemperatureWindow, VideoWindow,
)


class CapturePort(Protocol):
    """采集读取端口：四大模态完全对称的时序切片服务。

    调用方需要几秒，就给最后几秒（``seconds=None`` 给当前全量有效数据）；
    数据不足或无数据统一返回 ``None``，调用方直接取数判空，不存在探活布尔。
    """

    def release_recording(self, recording: "DetectionRecording") -> None: ...

    def begin_recording(self) -> "DetectionRecording": ...

    def is_connected(self) -> bool:
        """设备物理链路是否在线（WebRTC PeerConnection connected）。"""
        ...

    def video_window(self, seconds: float | None = None) -> VideoWindow | None:
        """实时视频尾窗；最新帧满 2 秒未更新时返回 None。

        seconds 控制尾部长度（右端对齐最新到达），不改变新鲜度；
        不传则取当前日志全量。封存检测通过 DetectionMedia 独立读取。
        """
        ...

    def audio_window(
        self, seconds: float | None = None, *, sample_rate: int = 48000,
    ) -> AudioWindow | None:
        """最后 seconds 秒的音频窗口（右端对齐最新到达）；无数据返回 None。"""
        ...

    def heart_rate_window(self, seconds: float | None = None) -> HeartRateWindow | None:
        """最后 seconds 秒的心率采样窗口；无数据返回 None。"""
        ...

    def temperature_window(self, seconds: float | None = None) -> TemperatureWindow | None:
        """最后 seconds 秒的体温采样窗口；无数据返回 None。"""
        ...


class DetectionMedia(Protocol):
    """One device generation. Segment offsets use milliseconds from acquisition."""

    def video_window(self) -> VideoWindow | None: ...
    def audio_window(self, *, sample_rate: int) -> AudioWindow | None: ...
    def audio_segment(self, start_ms: float, end_ms: float, sample_rate: int) -> AudioWindow | None: ...


class DetectionRecording(DetectionMedia, Protocol):
    def position_ms(self) -> float: ...
    def seal(self, connected: bool | None = None) -> "DetectionRecording": ...
    def release(self) -> None: ...
