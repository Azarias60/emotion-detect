"""Pure fatigue assessment tests; no model or hardware needed."""

from server.analysis.facts import fatigue_advice_fact
from server.analysis.fatigue.reporting.types import FatigueReportResult
from server.compute.fatigue.fatigue_advice.domain import generate_fatigue_advice
from server.compute.fatigue.fatigue_scoring.application import assess_behavior
from server.compute.fatigue.behavior_recognition.domain import summarize_behavior
from server.compute.fatigue.behavior_recognition.engine import FatigueAnalyzer
from server.compute.fatigue.types import FacialLandmarkExtractionResult
from server.detection.flows import fatigue_facts


def _behavior(*, blinks=10, yawns=0, nods=0, perclos=0.0, duration=60.0):
    return summarize_behavior(
        blink_count=blinks, yawn_count=yawns, nod_count=nods,
        closed_seconds=duration * perclos / 100.0, speaking_seconds=duration * 0.25,
        face_seconds=duration, video_seconds=duration,
    )


def test_assessment_consumes_behavior_result_and_preserves_thresholds():
    result = assess_behavior(_behavior(blinks=1, yawns=3, nods=2, perclos=25.0))
    assert (result.score, result.level, result.score_fraction) == (4, "重度疲劳", 1.0)
    assert assess_behavior(_behavior()).score == 0


def test_assessment_uses_unrounded_blink_rate():
    behavior = _behavior(blinks=1, duration=11.999)
    assert round(behavior.blink_frequency, 2) == 5.0
    assert behavior.blink_frequency > 5.0
    assert assess_behavior(behavior).score == 0


def test_missing_face_is_gated_to_none_before_assessment():
    # The engine returns None for a video without usable face frames, so the
    # assessment only ever receives real observations.
    analyzer = FatigueAnalyzer()
    for timestamp in (5.0, 5.1, 5.2):
        analyzer.process_frame(FacialLandmarkExtractionResult((), "no_face"), timestamp)
    assert assess_behavior(analyzer.behavior_result()) is None


def test_projection_composes_engine_observation_with_assessment():
    behavior = _behavior(blinks=1, yawns=3, nods=2, perclos=100.0)
    assessment = assess_behavior(behavior)
    measured = FatigueReportResult(
        behavior=behavior, fatigue=assessment,
        advice=generate_fatigue_advice(assessment),
        physiology=None, measurement_notice="未取得可用的生理指标",
        diagnostics=None,
    )
    facts = fatigue_facts(measured, detection_id="abc")
    behavior_body = facts["behavior"]
    assert behavior_body["face_coverage"] == 1.0
    assert behavior_body["blink_frequency"] == 1.0
    assert behavior_body["perclos_percent"] == 100.0
    # score_fraction 由疲劳事实投影归一化补充，与评估分档同源。
    assert facts["fatigue"] == {"score": 4, "level": "重度疲劳", "score_fraction": 1.0}
    assert facts["advice"] == fatigue_advice_fact(measured.advice)
    assert facts["measurement_notice"] == "未取得可用的生理指标"
    assert facts["detection_id"] == "abc"
    assert "status" not in behavior_body and "valid" not in behavior_body


def test_engine_observation_of_no_face_stays_unscored():
    assert FatigueAnalyzer().behavior_result() is None
