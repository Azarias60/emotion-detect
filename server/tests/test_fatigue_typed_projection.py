"""Fatigue observation projection gates; no model or hardware needed."""
import asyncio

import numpy as np
import pytest

from server.analysis.video import observe_behavior

from server.compute.fatigue.service import FatigueObservationService
from server.compute.fatigue.fatigue_advice.domain import generate_fatigue_advice
from server.compute.fatigue.fatigue_scoring.application import assess_behavior
from server.compute.fatigue.behavior_recognition.domain import summarize_behavior
from server.presentation.gateway.serializers import serialize_behavior_result
from server.analysis.fatigue.reporting.types import FatigueReportResult
from server.detection.flows import fatigue_facts


def observed(blinks=10, yawns=0, nods=0, perclos=0):
    behavior = summarize_behavior(
        blink_count=blinks, yawn_count=yawns, nod_count=nods,
        closed_seconds=0.6 * perclos, speaking_seconds=30.0, face_seconds=60.0, video_seconds=60.0,
    )
    return behavior, assess_behavior(behavior)


def _facts(behavior, assessment):
    measured = FatigueReportResult(
        behavior=behavior, fatigue=assessment,
        advice=generate_fatigue_advice(assessment),
        physiology=None, measurement_notice=None,
        diagnostics=None,
    )
    return fatigue_facts(measured, detection_id="d")


def test_zero_is_valid():
    behavior, assessment = observed()
    projected = serialize_behavior_result(behavior)
    assert projected["perclos_percent"] == 0.0
    assert "status" not in projected and "valid" not in projected
    assert _facts(behavior, assessment)["fatigue"] == {
        "score": 0, "level": "正常", "score_fraction": 0.0,
    }
    assert generate_fatigue_advice(assessment).level == "状态良好"


def test_high_score_projection():
    behavior, assessment = observed(blinks=1, yawns=3, nods=2, perclos=25)
    projected = _facts(behavior, assessment)["fatigue"]
    assert projected == {"score": 4, "level": "重度疲劳", "score_fraction": 1.0}
    assert serialize_behavior_result(behavior)["perclos_percent"] == 25.0


def _install_recognizer(recognize_frames):
    class _Recognizer:
        def observe_frames(self, frames):
            return recognize_frames(frames)

    service = FatigueObservationService(_Recognizer)
    service.load()
    return service


def test_observe_missing_window_never_touches_models():
    def fail(*args, **kwargs):
        raise AssertionError("缺失窗口不得触达行为模型")

    coordinator = _install_recognizer(fail)
    assert asyncio.run(observe_behavior(coordinator, None)) is None


def test_observe_propagates_model_failure():
    def fail(*args, **kwargs):
        raise RuntimeError("damaged landmark weights")

    coordinator = _install_recognizer(fail)
    with pytest.raises(RuntimeError, match="damaged landmark weights"):
        coordinator.result_frames(((12.0, np.zeros((1, 1, 3), dtype=np.uint8)),))


def test_observe_scores_valid_observation():

    class _Recognizer:
        def observe_frames(self, frames):
            return observed(blinks=1, yawns=3, nods=2, perclos=25)[0]

    coordinator = FatigueObservationService(_Recognizer)
    coordinator.load()
    behavior = coordinator.result_frames(((12.0, np.zeros((1, 1, 3), dtype=np.uint8)),))
    assessment = assess_behavior(behavior)
    assert (assessment.score, assessment.level) == (4, "重度疲劳")
