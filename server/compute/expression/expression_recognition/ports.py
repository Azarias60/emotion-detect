"""Use-case-owned ports for expression classification and timestamps."""

from typing import Any, Protocol, Sequence

from server.compute.expression.types import ExpressionObservation


class ExpressionClassifier(Protocol):
    def predict(self, face_images: Sequence[Any]) -> tuple[ExpressionObservation, ...]: ...


class ExpressionRecognitionClock(Protocol):
    """Clock port; timestamp is captured after model inference completes."""

    def now(self) -> float: ...
