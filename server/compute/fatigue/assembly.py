"""Fatigue-domain factories and resource composition."""

from server import settings
from server.compute.fatigue.service import FatigueObservationService
from server.compute.fatigue.facial_landmarks.adapters.dlib_68 import Dlib68LandmarkReader
from server.compute.fatigue.behavior_recognition.adapters.video import VideoBehaviorRecognizer


def create_video_fatigue_processor() -> VideoBehaviorRecognizer:
    """Bind one application-owned dlib reader to the frame processor."""
    return VideoBehaviorRecognizer(Dlib68LandmarkReader(settings.FATIGUE_LANDMARKS_PATH))


def create_fatigue_observation_service() -> FatigueObservationService:
    return FatigueObservationService(create_video_fatigue_processor)
