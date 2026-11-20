"""Stream timestamped BGR images through the fatigue landmark reader."""

from collections.abc import Iterable

import cv2

from server.compute.fatigue.behavior_recognition.engine import FatigueAnalyzer
from server.compute.fatigue.facial_landmarks.ports import FacialLandmarkReader
from server.compute.fatigue.facial_landmarks.service import FacialLandmarkExtraction
from server.compute.fatigue.types import BehaviorObservation, TimestampedImage


class VideoBehaviorRecognizer:
    """Preserve source orientation when resizing for the dlib face detector."""

    def __init__(self, reader: FacialLandmarkReader):
        self._reader = reader

    def close(self) -> None:
        self._reader.close()

    def observe_frames(self, frames: Iterable[TimestampedImage]) -> BehaviorObservation | None:
        extractor = FacialLandmarkExtraction(self._reader)
        analyzer = FatigueAnalyzer()
        for timestamp, bgr in frames:
            size = (480, 640) if bgr.shape[0] > bgr.shape[1] else (640, 480)
            image = cv2.resize(bgr, size)
            analyzer.process_frame(extractor.execute(image), timestamp)
        return analyzer.behavior_result()
