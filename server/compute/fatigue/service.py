"""Transport-free public entry points for the fatigue domain."""

import threading
from collections.abc import Callable, Iterable

from server.compute.fatigue.types import (
    BehaviorObservation,
    BehaviorPort,
    FatigueAdvice,
    FatigueAssessment,
    FatigueVideoProcessor,
    TimestampedImage,
)

from server.compute.fatigue.fatigue_advice.domain import generate_fatigue_advice
from server.compute.fatigue.fatigue_scoring.application import assess_behavior


class FatigueObservationService(BehaviorPort):
    """公开观察入口；视频处理器工厂由 assembly 注入，本模块不引用适配器类型。"""

    def __init__(self, processor_factory: Callable[[], FatigueVideoProcessor]) -> None:
        self._processor_factory = processor_factory
        self._processor: FatigueVideoProcessor | None = None
        self._lock = threading.Lock()

    def load(self) -> None:
        self._processor = self._processor_factory()

    def close(self) -> None:
        with self._lock:
            processor, self._processor = self._processor, None
            if processor is not None:
                processor.close()

    def result_frames(self, frames: Iterable[TimestampedImage]) -> BehaviorObservation | None:
        with self._lock:
            if self._processor is None:
                raise RuntimeError("FatigueObservationService is not loaded")
            return self._processor.observe_frames(frames)


class FatigueScoringService:
    def result(self, observation: BehaviorObservation | None) -> FatigueAssessment | None:
        return assess_behavior(observation)


class FatigueAdviceService:
    def result(self, assessment: FatigueAssessment | None) -> FatigueAdvice:
        return generate_fatigue_advice(assessment)


__all__ = [
    "FatigueAdviceService",
    "FatigueObservationService",
    "FatigueScoringService",
]
