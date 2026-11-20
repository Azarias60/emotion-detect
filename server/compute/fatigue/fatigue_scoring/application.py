"""Interpret an independent behavior observation as a fatigue score."""

from server.compute.fatigue.types import BehaviorObservation, FatigueAssessment
from server.compute.fatigue.fatigue_scoring.domain import assess_fatigue


def assess_behavior(behavior: BehaviorObservation | None) -> FatigueAssessment | None:
    """Score a real behavior observation; no observation means no assessment."""
    if behavior is None:
        return None
    return assess_fatigue(
        perclos_percent=behavior.perclos_percent,
        blink_frequency=behavior.blink_frequency,
        yawn_count=behavior.yawn_count,
        nod_frequency=behavior.nod_frequency,
    )
