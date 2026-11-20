"""Outbound port for model-specific landmark readers."""

from typing import Any, Protocol

from server.compute.fatigue.types import FacialLandmarkExtractionResult


class FacialLandmarkReader(Protocol):
    """Extract landmarks from a BGR image without exposing model-library objects."""

    def close(self) -> None: ...

    def extract(
        self,
        image: Any,
    ) -> FacialLandmarkExtractionResult: ...
