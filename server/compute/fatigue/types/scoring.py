"""Independent fatigue assessment payload."""

from dataclasses import dataclass


@dataclass(frozen=True)
class FatigueAssessment:
    score: int
    level: str

    @property
    def score_fraction(self) -> float:
        """Legacy normalized score, not a calibrated probability."""
        return round(self.score / 4.0, 4)
