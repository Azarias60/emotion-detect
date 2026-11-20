"""Measurement 端口契约：内存帧输入与生理结果读取器。

无可用指标以 ``None`` 表达，部分缺测保留可用指标。视频推理或读取异常向调用方传播。
"""

from typing import Protocol
from dataclasses import dataclass
from collections.abc import Callable, Iterator

from server.compute.facephys.types.results import VideoPhysiologyMeasurement


@dataclass(frozen=True)
class VideoAnalysisInput:
    """独立迭代一次采集窗口，逐项返回设备拍摄时间秒和规范方向的 BGR 帧。"""

    frames: Callable[[], Iterator]


class VideoPhysiologyReader(Protocol):
    """一次视频 pass 的全部生理结果；无可用数据返回 ``None``。"""

    def result(
        self, source: "VideoAnalysisInput | None",
    ) -> VideoPhysiologyMeasurement | None: ...


__all__ = ["VideoAnalysisInput", "VideoPhysiologyReader"]
