"""Expression sample aggregation policy."""

from typing import Iterable

from server.compute.expression.types import ExpressionObservation, ExpressionObservations

def summarize_expressions(observations: Iterable[ExpressionObservation]) -> ExpressionObservations:
    """Aggregate model samples in first-seen order, without altering evidence."""
    samples = tuple(observations)
    counts: dict[str, int] = {}
    for sample in samples:
        counts[sample.label] = counts.get(sample.label, 0) + 1
    return ExpressionObservations(
        observations=samples,
        class_counts=tuple(counts.items()),
    )
