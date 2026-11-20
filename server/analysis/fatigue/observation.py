"""Observe behavior in the latest captured video window."""

from dataclasses import dataclass

from server.analysis.video import observe_behavior
from server.capture.types import CapturePort
from server.compute.fatigue.types import BehaviorObservation, BehaviorPort
from server.detection_errors import DetectionInputError


@dataclass(frozen=True)
class BehaviorObservationService:
    capture: CapturePort
    behavior: BehaviorPort

    async def observe_behavior(self) -> BehaviorObservation:
        result = await observe_behavior(self.behavior, self.capture.video_window())
        if result is None:
            raise DetectionInputError("暂无已采集的视频画面，请先完成检测")
        return result
