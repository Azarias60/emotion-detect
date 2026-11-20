"""Frame coordination preserves behavior observations without model inference."""
import asyncio
from threading import get_ident

import numpy as np
import pytest

from server.analysis.video import observe_behavior
from server.capture.types import Frame

from server.compute.fatigue.fatigue_scoring.application import assess_behavior
from server.compute.fatigue.behavior_recognition.domain import summarize_behavior
from server.compute.fatigue.service import FatigueObservationService


class StubObserver:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error
        self.calls = []

    def close(self):
        pass

    def observe_frames(self, frames):
        self.calls.append(list(frames))
        if self.error is not None:
            raise self.error
        return self.result


def _loaded_service(processor):
    service = FatigueObservationService(lambda: processor)
    service.load()
    return service


def _behavior(*, blinks=1, yawns=3, nods=2, perclos=25.0):
    return summarize_behavior(
        blink_count=blinks, yawn_count=yawns, nod_count=nods,
        closed_seconds=0.6 * perclos, speaking_seconds=0.0, face_seconds=60.0, video_seconds=60.0,
    )


def test_missing_window_never_invokes_observer():
    observer = StubObserver(error=AssertionError("should not be invoked"))
    coordinator = _loaded_service(observer)
    assert asyncio.run(observe_behavior(coordinator, None)) is None
    assert observer.calls == []


def test_observation_result_is_consumed_without_observer_ownership():
    behavior = _behavior()
    observer = StubObserver(result=behavior)
    coordinator = _loaded_service(observer)
    frames = [_frame(12.0), _frame(12.13)]
    result = coordinator.result_frames(iter(frames))
    (samples,) = observer.calls
    assert all(actual is original for actual, original in zip(samples, frames, strict=True))
    assert result is behavior
    assessment = assess_behavior(result)
    assert (assessment.score, assessment.level, assessment.score_fraction) == (4, "重度疲劳", 1.0)


def test_no_usable_face_sample_is_observed_as_none():
    # The engine gates no-face/insufficient samples to None before the
    # coordinator sees them; scoring never receives a fabricated observation.
    coordinator = _loaded_service(StubObserver(result=None))
    assert coordinator.result_frames((_frame(12.0),)) is None


def test_reader_failure_propagates():
    coordinator = _loaded_service(StubObserver(error=RuntimeError("damaged weights")))
    with pytest.raises(RuntimeError, match="damaged weights"):
        coordinator.result_frames((_frame(12.0),))


def test_zero_score_is_valid():
    result = _loaded_service(
        StubObserver(_behavior(blinks=10, yawns=0, nods=0, perclos=0.0)),
    ).result_frames((_frame(12.0),))
    assessment = assess_behavior(result)
    assert (assessment.score, assessment.level) == (0, "正常")


def _frame(timestamp):
    return timestamp, np.zeros((1, 1, 3), dtype=np.uint8)


def test_analysis_projects_video_lazily_in_the_worker_with_original_images():
    event_loop_thread = get_ident()
    images = [np.zeros((2, 2, 3), dtype=np.uint8) for _ in range(2)]

    class Window:
        def __iter__(self):
            assert get_ident() != event_loop_thread
            yield Frame(images[0], 37.01)
            assert first_sample_consumed
            yield Frame(images[1], 37.23)

    first_sample_consumed = False
    result = _behavior()

    class Observer:
        def result_frames(self, frames):
            nonlocal first_sample_consumed
            samples = iter(frames)
            timestamp, image = next(samples)
            assert timestamp == 37.01
            assert image is images[0]
            first_sample_consumed = True
            timestamp, image = next(samples)
            assert timestamp == 37.23
            assert image is images[1]
            assert next(samples, None) is None
            return result

    assert asyncio.run(observe_behavior(Observer(), Window())) is result
