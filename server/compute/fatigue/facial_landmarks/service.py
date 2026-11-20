"""Facial landmark extraction use case."""

from typing import Any

from server.compute.fatigue.types import FacialLandmarkExtractionResult
from server.compute.fatigue.facial_landmarks.ports import FacialLandmarkReader


class FacialLandmarkExtraction:
    """Coordinates one BGR image observation."""

    def __init__(self, reader: FacialLandmarkReader):
        self._reader = reader

    def execute(
        self,
        image: Any,
    ) -> FacialLandmarkExtractionResult:
        if image is None:
            return FacialLandmarkExtractionResult(
                faces=(),
                status="missing_image",
            )
        return self._reader.extract(image)
