"""MobileNetV2 classifier adapter around the existing pinned inference engine."""

from typing import Any, Sequence

from server.compute.expression.types import ExpressionObservation


class MobileNetV2ExpressionClassifier:
    """Load the existing classifier lazily and expose model-independent observations."""

    def __init__(self, engine: Any, class_names: Sequence[str]):
        self._engine = engine
        self._class_names = tuple(class_names)

    @classmethod
    def create(cls, cfg: Any) -> "MobileNetV2ExpressionClassifier":
        from server.vendor.classifier import inference
        engine = inference.Inference(cfg)
        return cls(engine, cfg.class_name)

    def predict(self, face_images: Sequence[Any]) -> tuple[ExpressionObservation, ...]:
        if len(face_images) == 0:
            return ()
        indices, scores = self._engine.inference(face_images)
        return tuple(
            ExpressionObservation(
                label=self._class_names[int(index)],
                confidence=float(score),
                timestamp=None,
                class_index=int(index),
            )
            for index, score in zip(indices, scores)
        )
