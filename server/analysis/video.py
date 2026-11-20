"""Adapt video evidence for observations and reports."""

from collections.abc import Iterator

from server.capture.types import VideoWindow
from server.compute.facephys.types import (
    PhysiologyDiagnostics, VideoAnalysisInput, VideoPhysiologyMeasurement, VideoPhysiologyReader,
)
from server.compute.fatigue.types import (
    BehaviorObservation,
    BehaviorPort,
    FatigueAdvice,
    FatigueAdvicePort,
    FatigueAssessment,
    FatigueScoringPort,
    TimestampedImage,
)
from server.concurrency import run_blocking


def _timestamped_images(window: VideoWindow) -> Iterator[TimestampedImage]:
    for frame in window:
        yield frame.timestamp, frame.data


async def observe_behavior(behavior: BehaviorPort, window: VideoWindow | None) -> BehaviorObservation | None:
    if window is None:
        return None
    return await run_blocking(behavior.result_frames, _timestamped_images(window))


def assess_behavior(
    observation: BehaviorObservation | None,
    scoring: FatigueScoringPort,
    advice: FatigueAdvicePort,
) -> tuple[FatigueAssessment | None, FatigueAdvice]:
    assessment = scoring.result(observation)
    return assessment, advice.result(assessment)


async def measure_video_physiology(
    reader: VideoPhysiologyReader, window: VideoWindow | None,
) -> VideoPhysiologyMeasurement | None:
    """Preserve normal missing measurements and propagate reader failures."""
    if window is None:
        return None
    source = VideoAnalysisInput(frames=lambda: _timestamped_images(window))
    return await run_blocking(reader.result, source)

def physiology_diagnostics(
    measurement: VideoPhysiologyMeasurement | None, window: VideoWindow | None,
) -> PhysiologyDiagnostics | None:
    if window is None:
        return PhysiologyDiagnostics("no_video", "no_video", None, 0.0)
    return measurement.diagnostics if measurement else PhysiologyDiagnostics("no_signal", "no_signal", None, 0.0)
