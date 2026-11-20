"""Regression checks for integrating pure assessment policy with typed behavior observations.

Model startup is intentionally bypassed: these tests only exercise the
observation/aggregation interfaces, not dlib/OpenCV inference.
"""

from server.analysis.facts import (
    behavior_observation_fact, fatigue_assessment_fact,
)
from server.analysis.fatigue.reporting.types import FatigueReportResult
from server.compute.fatigue.fatigue_advice.domain import generate_fatigue_advice
from server.compute.fatigue.fatigue_scoring.application import assess_behavior
from server.compute.fatigue.behavior_recognition.engine import FatigueAnalyzer
from server.compute.fatigue.behavior_recognition.domain import summarize_behavior
from server.compute.fatigue.types import FacialLandmarkExtractionResult
from server.presentation.gateway.serializers import serialize_behavior_result
from server.detection.flows import fatigue_facts


def _observed_behavior(*, blinks, yawns, nods, closed_seconds):
    return summarize_behavior(
        blink_count=blinks, yawn_count=yawns, nod_count=nods,
        closed_seconds=closed_seconds, speaking_seconds=30.0,
        face_seconds=60.0, video_seconds=60.0,
    )


def _measured(behavior, assessment):
    return FatigueReportResult(
        behavior=behavior, fatigue=assessment,
        advice=generate_fatigue_advice(assessment),
        physiology=None, measurement_notice=None,
        diagnostics=None,
    )


def test_projection_keeps_unchanged_high_score_keys():
    behavior = _observed_behavior(blinks=1, yawns=3, nods=2, closed_seconds=15.0)
    assessment = assess_behavior(behavior)
    assert serialize_behavior_result(behavior) == {
        "blink_count": 1, "blink_frequency": 1.0,
        "yawn_count": 3, "nod_count": 2, "nod_frequency": 2.0,
        "perclos_percent": 25.0, "speaking_percent": 50.0,
        "face_coverage": 1.0,
    }
    facts = fatigue_facts(_measured(behavior, assessment), detection_id=9)
    assert facts["fatigue"] == {
        "score": 4, "level": "重度疲劳", "score_fraction": 1.0,
    }
    assert facts["behavior"] == behavior_observation_fact(behavior)


def test_projection_keeps_unchanged_zero_score_keys():
    behavior = _observed_behavior(blinks=10, yawns=0, nods=0, closed_seconds=0.0)
    assessment = assess_behavior(behavior)
    facts = fatigue_facts(_measured(behavior, assessment), detection_id=9)
    assert facts["fatigue"] == {
        "score": 0, "level": "正常", "score_fraction": 0.0,
    }
    assert facts["behavior"]["perclos_percent"] == 0.0
    assert fatigue_assessment_fact(assessment) == {"score": 0, "level": "正常"}


def test_behavior_result_exposes_observations_without_fatigue_policy():
    result = _observed_behavior(blinks=1, yawns=3, nods=2, closed_seconds=15.0)
    assert result.blink_frequency == 1.0
    assert result.perclos_percent == 25.0
    assert not hasattr(result, "fatigue_score")
    assert not hasattr(result, "status")


def test_behavior_result_collapses_no_face_and_empty_video_to_none():
    analyzer = FatigueAnalyzer()
    assert analyzer.behavior_result() is None
    for timestamp in (2.0, 2.1, 2.2):
        analyzer.process_frame(FacialLandmarkExtractionResult((), "no_face"), timestamp)
    assert analyzer.behavior_result() is None
