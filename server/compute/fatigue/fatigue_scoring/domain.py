"""Pure fatigue scoring rules."""

from server.compute.fatigue.types import FatigueAssessment

FATIGUE_PERCLOS_THRESH = 20.0
FATIGUE_BLINK_FREQ_THRESH = 5
FATIGUE_YAWN_THRESH = 3
FATIGUE_NOD_FREQ_THRESH = 2


def assess_fatigue(*, perclos_percent: float, blink_frequency: float,
                   yawn_count: int, nod_frequency: float) -> FatigueAssessment:
    """Assess fatigue from observable behavior using existing thresholds."""
    score = sum((
        perclos_percent >= FATIGUE_PERCLOS_THRESH,
        blink_frequency <= FATIGUE_BLINK_FREQ_THRESH,
        yawn_count >= FATIGUE_YAWN_THRESH,
        nod_frequency >= FATIGUE_NOD_FREQ_THRESH,
    ))
    level = "重度疲劳" if score >= 4 else "中度疲劳" if score == 3 else "轻度疲劳" if score == 2 else "正常"
    return FatigueAssessment(score=int(score), level=level)
