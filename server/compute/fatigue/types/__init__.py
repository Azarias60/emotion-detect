"""Public types owned by the fatigue compute domain."""

from collections.abc import Iterable
from typing import Protocol

import numpy as np

from .behavior import BehaviorObservation
from .advice import FatigueAdvice
from .scoring import FatigueAssessment
from .landmarks import (
    FaceLandmarks,
    FacialLandmarkExtractionResult,
    LandmarkPoint,
)


TimestampedImage = tuple[float, np.ndarray]


class BehaviorPort(Protocol):
    """Consume (device timestamp in seconds, canonical BGR image) pairs in order.

    The first frame at each timestamp is authoritative. Nonfinite or backwards
    timestamps raise DetectionInputError. Empty or all-no-face input yields None.
    """

    def result_frames(self, frames: Iterable[TimestampedImage]) -> BehaviorObservation | None: ...


class FatigueVideoProcessor(Protocol):
    def observe_frames(self, frames: Iterable[TimestampedImage]) -> BehaviorObservation | None: ...

    def close(self) -> None: ...


class FatigueScoringPort(Protocol):
    def result(self, observation: BehaviorObservation | None) -> FatigueAssessment | None: ...


class FatigueAdvicePort(Protocol):
    def result(self, assessment: FatigueAssessment | None) -> FatigueAdvice: ...


__all__ = [
    "BehaviorObservation",
    "BehaviorPort",
    "FaceLandmarks",
    "FacialLandmarkExtractionResult",
    "FatigueAdvice",
    "FatigueAdvicePort",
    "FatigueAssessment",
    "FatigueScoringPort",
    "FatigueVideoProcessor",
    "LandmarkPoint",
    "TimestampedImage",
]
