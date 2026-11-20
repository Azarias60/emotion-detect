"""dlib-68 facial landmark types in source-image pixel coordinates.

This capability serves fatigue behavior analysis: one face, 68 pixel-space
points per frame. The FaceMesh path (normalized coordinates, rPPG ROI
sampling) lives in ``server.face_mesh_landmarks`` and shares no types with
this module.
"""

from dataclasses import dataclass
from math import isfinite
from typing import Literal

LandmarkModelSource = Literal["dlib_68"]
LandmarkCoordinateSpace = Literal["pixel"]
LandmarkStatus = Literal[
    "ok",
    "no_face",
    "missing_image",
    "invalid_image",
    "invalid_landmarks",
]
LandmarkQuality = Literal[
    "model_reported",
    "not_reported",
    "insufficient",
    "unknown",
]
LandmarkFailureReason = Literal[
    "invalid_image",
    "empty_landmarks",
    "invalid_landmarks",
]


@dataclass(frozen=True)
class LandmarkPoint:
    """One model-ordered landmark; coordinates retain the adapter's declared space."""

    x: float
    y: float
    z: float | None = None
    visibility: float | None = None
    presence: float | None = None

    def __post_init__(self):
        for name in ("x", "y", "z", "visibility", "presence"):
            value = getattr(self, name)
            if value is not None and not isfinite(float(value)):
                raise ValueError(f"{name} must be finite when provided")
        for name in ("visibility", "presence"):
            value = getattr(self, name)
            if value is not None and not 0.0 <= float(value) <= 1.0:
                raise ValueError(f"{name} must be between 0 and 1")


@dataclass(frozen=True)
class FaceLandmarks:
    """dlib-68 landmarks for one face, in source-image pixel coordinates."""

    points: tuple[LandmarkPoint, ...]
    coordinate_space: LandmarkCoordinateSpace = "pixel"
    image_width: int = 1
    image_height: int = 1

    def __post_init__(self):
        if self.coordinate_space != "pixel":
            raise ValueError("dlib-68 结果只允许像素坐标；FaceMesh 归一化坐标属于 face_mesh_landmarks 模块")
        if not self.points:
            raise ValueError("a face must contain at least one landmark")
        if self.image_width <= 0 or self.image_height <= 0:
            raise ValueError("source image dimensions must be positive")


@dataclass(frozen=True)
class FacialLandmarkExtractionResult:
    """Typed extraction outcome; model failures never masquerade as a face/no-face result."""

    faces: tuple[FaceLandmarks, ...]
    status: LandmarkStatus
    model_source: LandmarkModelSource | None = None
    quality: LandmarkQuality = "unknown"
    reason: LandmarkFailureReason | None = None

    def __post_init__(self):
        if self.status not in (
            "ok", "no_face", "missing_image", "invalid_image",
            "invalid_landmarks",
        ):
            raise ValueError("unknown facial-landmark status")
        if self.model_source not in (None, "dlib_68"):
            raise ValueError("dlib-68 结果不允许其他模型来源；FaceMesh 属于 face_mesh_landmarks 模块")

        if self.status == "ok":
            if not self.faces:
                raise ValueError("an ok result requires faces")
            if self.quality not in ("model_reported", "not_reported", "unknown"):
                raise ValueError("an ok result requires an explicit quality-reporting state")
            if self.reason is not None:
                raise ValueError("an ok result cannot carry a failure reason")
            return

        if self.faces:
            raise ValueError("non-ok results cannot contain faces")
        if self.status == "missing_image":
            if self.model_source is not None or self.quality != "unknown" or self.reason is not None:
                raise ValueError("missing-image results must not imply model execution")
        elif self.status == "no_face":
            if self.reason is not None:
                raise ValueError("no-face results cannot carry a failure reason")
        elif self.status == "invalid_image":
            if self.model_source is None or self.quality != "insufficient" or self.reason != "invalid_image":
                raise ValueError("invalid-image results require explicit input failure semantics")
        elif self.status == "invalid_landmarks":
            if self.model_source is None or self.quality != "insufficient":
                raise ValueError("invalid-landmark results require model provenance and insufficient quality")
            if self.reason not in ("empty_landmarks", "invalid_landmarks"):
                raise ValueError("invalid-landmark results require an explicit reason")
