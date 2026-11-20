"""Emotion-only fact shape. Shared facts are defined in server.analysis.facts."""

from typing_extensions import TypedDict

from server.compute.expression.types import ExpressionObservations


class ExpressionObservationFact(TypedDict):
    label: str
    confidence: float | None
    timestamp: float | None
    class_index: int | None


class ExpressionFact(TypedDict):
    """The complete expression evidence admitted to emotion reports."""

    observations: tuple[ExpressionObservationFact, ...]
    class_counts: tuple[tuple[str, int], ...]


def expression_fact(value: ExpressionObservations | None) -> ExpressionFact | None:
    """Whitelist each published field; never serialize upstream extensions."""
    if value is None:
        return None
    return {
        "observations": tuple({
            "label": item.label,
            "confidence": item.confidence,
            "timestamp": item.timestamp,
            "class_index": item.class_index,
        } for item in value.observations),
        "class_counts": value.class_counts,
    }
