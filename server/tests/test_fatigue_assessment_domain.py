"""Fatigue domain assessment is independent of concrete vision-model infrastructure."""

import pytest

from server.compute.fatigue.fatigue_scoring.domain import assess_fatigue


@pytest.mark.parametrize("flags,expected_score,expected_level", [
    ((False, False, False, False), 0, "正常"),
    ((True, False, False, False), 1, "正常"),
    ((True, True, False, False), 2, "轻度疲劳"),
    ((True, True, True, False), 3, "中度疲劳"),
    ((True, True, True, True), 4, "重度疲劳"),
])
def test_score_parity(flags, expected_score, expected_level):
    perclos, slow_blinks, yawns, nods = flags
    decision = assess_fatigue(
        perclos_percent=20.0 if perclos else 19.99,
        blink_frequency=5.0 if slow_blinks else 5.01,
        yawn_count=3 if yawns else 2,
        nod_frequency=2.0 if nods else 1.99,
    )
    assert (decision.score, decision.level, decision.score_fraction) == (
        expected_score, expected_level, expected_score / 4.0,
    )


def test_no_media_framework_dependency():
    from dataclasses import FrozenInstanceError
    assessment = assess_fatigue(perclos_percent=0, blink_frequency=6, yawn_count=0, nod_frequency=0)
    with pytest.raises(FrozenInstanceError):
        assessment.score = 3
