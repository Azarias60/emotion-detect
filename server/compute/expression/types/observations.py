"""Model-independent expression observations and recognition summary.

A sampled expression is evidence, not a person's inferred emotion. Missing
confidence or timestamps remain None; no new frame/time semantics are implied.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class ExpressionObservation:
    label: str
    confidence: float | None
    timestamp: float | None
    class_index: int | None = None


@dataclass(frozen=True)
class ExpressionObservations:
    observations: tuple[ExpressionObservation, ...]
    class_counts: tuple[tuple[str, int], ...]

    def __post_init__(self):
        counts: dict[str, int] = {}
        for observation in self.observations:
            counts[observation.label] = counts.get(observation.label, 0) + 1
        if self.class_counts != tuple(counts.items()):
            raise ValueError("class counts must match observations and preserve first-seen order")
