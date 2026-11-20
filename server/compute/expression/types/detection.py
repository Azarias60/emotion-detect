"""Model-independent face detection result contract."""

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class FaceDetection:
    x1: float
    y1: float
    x2: float
    y2: float
    confidence: float
    label_id: int


@dataclass(frozen=True)
class FaceDetectionResult:
    detections: tuple[FaceDetection, ...]
    status: Literal["ok", "no_face", "missing_image"]

    def __post_init__(self):
        if self.status not in ("ok", "no_face", "missing_image"):
            raise ValueError("unknown face-detection status")
        if (self.status == "ok") != bool(self.detections):
            raise ValueError("status must agree with whether faces were detected")
