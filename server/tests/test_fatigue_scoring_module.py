from dataclasses import fields

from server.compute.fatigue.types import BehaviorObservation
from server.compute.fatigue.fatigue_scoring.application import assess_behavior
from server.compute.fatigue.types import FatigueAssessment


def _behavior(**changes):
    values = {
        "blink_count": 5,
        "blink_frequency": 5.0,
        "yawn_count": 3,
        "nod_count": 2,
        "nod_frequency": 2.0,
        "perclos_percent": 20.0,
        "speaking_percent": 0.0,
        "face_coverage": 1.0,
    }
    values.update(changes)
    return BehaviorObservation(**values)

def test_assessment_payload_defines_no_envelope_fields():
    assert [field.name for field in fields(FatigueAssessment)] == ["score", "level"]


def test_scoring_none_observation_returns_none():
    assert assess_behavior(None) is None


def test_assess_behavior_scores_real_observation():
    result = assess_behavior(_behavior())
    assert (result.score, result.level, result.score_fraction) == (4, "重度疲劳", 1.0)
