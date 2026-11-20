"""采集会话领域对象：会话数据独立于设备实时缓冲。"""
from dataclasses import dataclass
from typing import Literal, Protocol

import numpy as np


@dataclass(frozen=True, eq=False)
class Frame:
    """采集统一交付的规范化帧：画面与设备拍摄时刻一体交付。

    - ``data`` 为规范方向的 BGR ndarray（解码产物，零拷贝引用）；
    - ``timestamp`` 为拍摄时刻（设备时钟秒），需要 dt 的消费者
      直接取用，不需要的消费者忽略即可；
    - 恒等语义（``eq=False``）与 ndarray 一致：逐字段比较会触发
      ndarray 的数组相等语义，哈希更是直接不可用。
    """

    data: np.ndarray
    timestamp: float


HardwareHeartRateSource = Literal['mcu', 'ld6002_radar']


@dataclass(frozen=True)
class HeartRateWindow:
    """MCU heart-rate evidence, independent of model-based video estimates."""

    samples_bpm: tuple[float, ...] = ()
    average_bpm: float | None = None
    latest_bpm: float | None = None
    live_samples_bpm: tuple[float, ...] = ()
    source: HardwareHeartRateSource = "mcu"


@dataclass(frozen=True)
class TemperatureWindow:
    """MCU ambient and object temperature samples."""

    ambient_temps: tuple[float, ...] = ()
    object_temps: tuple[float, ...] = ()
    avg_ambient_temp: float | None = None
    avg_object_temp: float | None = None
    latest_ambient_temp: float | None = None
    latest_object_temp: float | None = None


@dataclass(frozen=True)
class AudioWindow:
    """一段任务范围的 PCM16 单声道音频快照；采样率由请求方声明。

    ``received_at`` 为窗口右端（最近一次音频到达）的服务器单调时钟时刻，
    供展示层计算时延；无数据语义由端口层以 ``None`` 表达。
    """

    pcm: bytes
    sample_rate: int
    received_at: float | None = None


class CanonicalFrameDecoder(Protocol):
    """规范帧解码器契约：JPEG 字节 → 规范方向 BGR 帧，坏帧返回 None。

    领域层只依赖本契约；具体实现（OpenCV 等）由基础设施层注入。
    """

    def decode(self, jpeg: bytes) -> np.ndarray | None: ...


class VideoWindow:
    """一次采集视频窗口的只读快照（帧日志引用）。

    - ``__iter__`` 每次返回一个从头开始的独立流式迭代器，逐帧把 JPEG
      按需解码并做规范方向归一化，统一交付 :class:`Frame`（画面 +
      设备拍摄时刻）；不整体物化帧列表，解码失败的帧跳过，窗口绝不
      提供任何单帧特例接口。视图取舍归消费者：只要画面的取
      ``frame.data``，需要 dt 的取 ``frame.timestamp``。
    - 快照持有日志字节引用，创建后的新帧、清空或设备代际替换不影响
      已取得的窗口；解码器由构造注入，领域对象不感知编解码实现。
    """

    def __init__(self, journal: tuple[tuple[float, bytes, float], ...],
                 decoder: CanonicalFrameDecoder):
        self._journal = journal  # ((received_at, jpeg_bytes, captured_at), ...)
        self._decoder = decoder

    def __iter__(self):
        for _, jpeg, captured_at in self._journal:
            frame = self._decoder.decode(jpeg)
            if frame is not None:
                yield Frame(data=frame, timestamp=captured_at)


__all__ = [
    "AudioWindow",
    "CanonicalFrameDecoder",
    "Frame",
    "HeartRateWindow",
    "TemperatureWindow",
    "VideoWindow",
]
