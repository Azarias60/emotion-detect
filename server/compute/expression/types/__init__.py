"""Public types owned by the expression compute domain.

The sub-capabilities remain implementation details of this domain.  Callers
receive the typed expression payload and the one video-port protocol from this
module instead of importing face detection or classifier implementations.
"""

from typing import Protocol

from .observations import (
    ExpressionObservation,
    ExpressionObservations,
)
from .detection import (
    FaceDetection,
    FaceDetectionResult,
)


class ExpressionVideoPredictor(Protocol):
    def recognize_frames(self, frames, interval: int = 1) -> ExpressionObservations: ...


class ExpressionPort(Protocol):
    """表情观察端口：输入为帧迭代器工厂（无窗口时传 None）。"""

    def result_frames(self, frames) -> ExpressionObservations | None: ...


__all__ = [
    "ExpressionObservation",
    "ExpressionObservations",
    "ExpressionPort",
    "ExpressionVideoPredictor",
    "FaceDetection",
    "FaceDetectionResult",
]
