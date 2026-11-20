"""Independent fatigue advice payload."""

from dataclasses import dataclass


@dataclass(frozen=True)
class FatigueAdvice:
    level: str
    text: str
