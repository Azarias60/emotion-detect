"""Boundary ports the Gateway composes for the tongue observation."""

from typing import Protocol

from server.compute.tongue.types.results import TongueResult


class TonguePort(Protocol):
    """望诊观察端口：与表情/行为同构的帧工厂入口。

    ``frames`` 为帧迭代器工厂（每次调用返回一个从头开始的独立迭代器）；
    ``None`` 表示无窗口。域内自持选帧策略，无可用帧返回 ``None``。
    """

    def result_frames(self, frames) -> TongueResult | None: ...


__all__ = ["TonguePort"]
