"""Public expression capability and its model lifecycle (in-memory frames)."""

import threading
from collections.abc import Callable

from server.compute.expression.types import ExpressionObservations, ExpressionVideoPredictor


class ExpressionVideoService:
    """Own the resident predictor and its inference lock, never task evidence."""

    def __init__(self, predictor_factory: Callable[[], ExpressionVideoPredictor]):
        self._predictor_factory = predictor_factory
        self._predictor: ExpressionVideoPredictor | None = None
        self._lock = threading.Lock()

    def load(self) -> None:
        self._predictor = self._predictor_factory()

    def close(self) -> None:
        with self._lock:
            self._predictor = None

    def result_frames(self, frames) -> ExpressionObservations | None:
        if frames is None:
            return None
        with self._lock:
            if self._predictor is None:
                raise RuntimeError("ExpressionVideoService is not loaded")
            observations = self._predictor.recognize_frames(frames=frames, interval=10)
        return observations if observations.observations else None
