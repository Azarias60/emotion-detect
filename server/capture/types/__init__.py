"""Public capability contracts used by report operations.

None denotes normal missing data; typed detection errors propagate to the caller.
Legacy cross-capability payload imports remain until phase three.
"""

from server.capture.domain import (
    AudioWindow, Frame, HardwareHeartRateSource, HeartRateWindow, TemperatureWindow, VideoWindow,
)
from .reads import CapturePort, DetectionMedia, DetectionRecording

__all__ = [
    "AudioWindow",
    "CapturePort",
    "Frame",
    "DetectionRecording",
    "DetectionMedia",
    "HardwareHeartRateSource",
    "HeartRateWindow",
    "TemperatureWindow",
    "VideoWindow",
]
