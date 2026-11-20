"""Generate a rule-based fatigue report from one sealed video window."""

from collections.abc import Callable
from dataclasses import dataclass

from server.analysis.facts import video_physiology_fact
from server.analysis.fatigue.reporting.types import FatigueReportResult
from server.analysis.video import assess_behavior, measure_video_physiology, observe_behavior, physiology_diagnostics
from server.capture.types import DetectionMedia
from server.compute.facephys.types import VideoPhysiologyReader
from server.compute.fatigue.types import BehaviorPort, FatigueAdvicePort, FatigueScoringPort
from server.detection_errors import DetectionInputError


@dataclass(frozen=True)
class FatigueAnalysisFlowService:
    behavior: BehaviorPort
    fatigue_scoring: FatigueScoringPort
    fatigue_advice: FatigueAdvicePort
    facephys_factory: Callable[[], VideoPhysiologyReader]

    async def run(self, media: DetectionMedia) -> FatigueReportResult:
        window = media.video_window()
        behavior = await observe_behavior(self.behavior, window)
        if behavior is None:
            raise DetectionInputError("本轮视频中没有有效的人脸行为数据，请重新检测")

        fatigue, advice = assess_behavior(behavior, self.fatigue_scoring, self.fatigue_advice)
        if fatigue is None:
            raise DetectionInputError("缺少有效疲劳评估结果")

        measurement = await measure_video_physiology(self.facephys_factory(), window)
        physiology = video_physiology_fact(measurement)

        return FatigueReportResult(
            behavior=behavior, fatigue=fatigue, advice=advice,
            physiology=physiology,
            diagnostics=physiology_diagnostics(measurement, window),
            measurement_notice="未取得可用的生理指标" if physiology is None else None,
        )
