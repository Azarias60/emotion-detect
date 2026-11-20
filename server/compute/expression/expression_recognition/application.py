"""Expression recognition use case, independent of classifier runtime details."""

from typing import Any, Sequence

from server.compute.expression.types import ExpressionObservation, ExpressionObservations
from server.compute.expression.expression_recognition.domain import summarize_expressions
from server.compute.expression.expression_recognition.ports import ExpressionClassifier, ExpressionRecognitionClock


class ExpressionRecognitionApplication:
    def __init__(self, classifier: ExpressionClassifier, clock: ExpressionRecognitionClock):
        self._classifier = classifier
        self._clock = clock

    def recognize(
        self, face_images: Sequence[Any], *, timestamp: float | None = None,
    ) -> ExpressionObservations:
        predictions = self._classifier.predict(face_images)
        captured_at = timestamp if timestamp is not None else self._clock.now()
        observations = tuple(
            ExpressionObservation(
                label=item.label,
                confidence=item.confidence,
                timestamp=captured_at if captured_at is not None else item.timestamp,
                class_index=item.class_index,
            )
            for item in predictions
        )
        return summarize_expressions(observations)
