"""采集记录的时间窗口与输入代际边界；不调度检测或生成报告。"""

from array import array
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from server.capture.service import CaptureKernel
from server.detection_errors import DetectionInputError


@pytest.fixture
def device():
    now = [100.0]
    capture = CaptureKernel(clock=lambda: now[0])
    capture.hub.current = SimpleNamespace(closed=False, peer=SimpleNamespace(connectionState="connected"))
    return now, capture


def write_sample(capture, value):
    capture.audio_buffer.extend(array("h", [value] * 4800).tobytes())
    capture.update_sensors({
        "run": 1,
        "temperature": {"valid": True, "sampled_at_us": value, "ambient_c": 24, "object_c": 36},
        "heart_rate": {"valid": True, "sampled_at_us": value, "bpm": value},
    })


def test_capture_excludes_buffered_data_and_retains_only_one_device_generation(device):
    now, capture = device
    write_sample(capture, 61)
    def frame(level):
        _, encoded = cv2.imencode(".jpg", np.full((8, 8, 3), level, np.uint8))
        capture.publish_frame(encoded.tobytes(), now[0], now[0])

    frame(30)
    media = capture.begin_recording()
    now[0] += 1
    # A repeated state message carries the same hardware sample as before begin.
    write_sample(capture, 61)
    write_sample(capture, 72)
    frame(60)
    now[0] += 2
    media.seal(True)
    write_sample(capture, 89)
    frame(90)
    capture.clear_media_state()
    assert media.heart_rate_window().samples_bpm == (72,)
    assert media.temperature_window().object_temps == (36,)
    assert media.audio_window(sample_rate=48000).pcm == array("h", [61] * 4800 + [72] * 4800).tobytes()
    assert [int(image.data.mean()) for image in media.video_window()] == [60]
    other = capture.begin_recording()
    capture.clear_media_state()
    with pytest.raises(DetectionInputError, match="换代"):
        other.seal(True)


def test_sixty_second_limit_is_independent_of_page_timer(device):
    now, capture = device
    media = capture.begin_recording()
    now[0] += 59
    write_sample(capture, 70)
    now[0] += 2
    write_sample(capture, 84)
    now[0] += 120
    media.seal(False)
    assert media.position_ms() == 60000
    assert media.heart_rate_window().samples_bpm == (70,)
    assert len(media.audio_window(sample_rate=48000).pcm) == 9600
