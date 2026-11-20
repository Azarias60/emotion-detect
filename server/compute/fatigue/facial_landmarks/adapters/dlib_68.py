"""dlib 68-point adapter for fatigue behavior features."""

import threading
from pathlib import Path
from typing import Any

import cv2

from server import settings
from server.compute.fatigue.types import (
    FaceLandmarks,
    FacialLandmarkExtractionResult,
    LandmarkPoint,
)

MODEL_SOURCE = "dlib_68"


def _load_landmark_tools(model_path: Path) -> tuple[Any, Any]:
    if not model_path.is_file():
        raise FileNotFoundError(str(model_path))
    import dlib

    return dlib.get_frontal_face_detector(), dlib.shape_predictor(str(model_path))


def _image_dimensions(image: Any) -> tuple[int, int] | None:
    try:
        shape = getattr(image, "shape", None)
        if shape is None or len(shape) != 3 or shape[2] != 3:
            return None
        height, width = int(shape[0]), int(shape[1])
    except (TypeError, ValueError, IndexError):
        return None
    return (width, height) if width > 0 and height > 0 else None


class Dlib68LandmarkReader:
    """Reads the dlib 68-point model in source-image pixel coordinates.

    The adapter accepts BGR frames, converts them to grayscale, and deliberately
    retains the existing first-face policy used by fatigue analysis.
    """

    def __init__(self, model_path: str | Path | None = None):
        self.model_path = Path(model_path or settings.FATIGUE_LANDMARKS_PATH)
        self._lock = threading.Lock()
        self._tools: tuple[Any, Any] | None = _load_landmark_tools(self.model_path)

    def close(self) -> None:
        with self._lock:
            self._tools = None

    def extract(
        self,
        image: Any,
    ) -> FacialLandmarkExtractionResult:
        if image is None:
            return FacialLandmarkExtractionResult(
                faces=(), status="missing_image",
            )

        dimensions = _image_dimensions(image)
        if dimensions is None:
            return FacialLandmarkExtractionResult(
                faces=(), status="invalid_image", model_source=MODEL_SOURCE,
                quality="insufficient",
                reason="invalid_image",
            )

        if self._tools is None:
            raise RuntimeError("Dlib68LandmarkReader is closed")

        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        detector, predictor = self._tools
        with self._lock:
            rectangles = detector(gray, 0)
            if not rectangles:
                return FacialLandmarkExtractionResult(
                    faces=(), status="no_face", model_source=MODEL_SOURCE,
                    quality="insufficient",
                )
            shape = predictor(gray, rectangles[0])
            raw_points = shape.parts()

        if not raw_points:
            return FacialLandmarkExtractionResult(
                faces=(), status="invalid_landmarks", model_source=MODEL_SOURCE,
                quality="insufficient",
                reason="empty_landmarks",
            )
        if len(raw_points) != 68:
            return FacialLandmarkExtractionResult(
                faces=(), status="invalid_landmarks", model_source=MODEL_SOURCE,
                quality="insufficient",
                reason="invalid_landmarks",
            )

        try:
            points = tuple(
                LandmarkPoint(x=float(point.x), y=float(point.y))
                for point in raw_points
            )
            face = FaceLandmarks(
                points=points,
                coordinate_space="pixel",
                image_width=dimensions[0],
                image_height=dimensions[1],
            )
        except (AttributeError, TypeError, ValueError, OverflowError):
            return FacialLandmarkExtractionResult(
                faces=(), status="invalid_landmarks", model_source=MODEL_SOURCE,
                quality="insufficient",
                reason="invalid_landmarks",
            )

        return FacialLandmarkExtractionResult(
            faces=(face,),
            status="ok",
            model_source=MODEL_SOURCE,
            quality="not_reported",
        )
