"""Observable behavior summary; independent of fatigue interpretation and models.

The result preserves unrounded rates because assessment thresholds must never
operate on presentation-rounded values. Closed-eye and speech percentages and event rates use valid face observation
time. Face coverage is a fraction of valid video observation time.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class BehaviorObservation:
    blink_count: int
    blink_frequency: float
    yawn_count: int
    nod_count: int
    nod_frequency: float
    perclos_percent: float
    speaking_percent: float
    face_coverage: float
