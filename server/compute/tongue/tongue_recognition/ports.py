"""Use-case-owned boundary for the four-dimensional tongue predictor."""

from typing import Mapping, Protocol


class TonguePredictor(Protocol):
    def predict(self, image_path: str) -> Mapping[str, Mapping[str, object]]: ...
    def predict_frame(self, frame) -> Mapping[str, Mapping[str, object]]: ...
