"""The capture adapter preserves upstream video inputs, isolation and errors."""

import numpy as np
import pytest

from server.compute.facephys.upstream import OpenRppgReader
from server.compute.facephys.types import VideoAnalysisInput


def source(frames):
    return VideoAnalysisInput(lambda: iter(frames))


def test_missing_or_empty_video_does_not_construct_a_model():
    def must_not_run():
        pytest.fail("unexpected model load")

    reader = OpenRppgReader(must_not_run)
    assert reader.result(None) is None
    assert reader.result(source([])) is None


def test_repeated_passes_feed_fresh_models_with_rgb_and_capture_time(monkeypatch):
    from server.compute.facephys import upstream

    models = []

    class Model:
        def __init__(self):
            self.frames = []
            self.closed = False
            models.append(self)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            self.closed = True

        def update_frame(self, frame, ts):
            assert not self.closed
            self.frames.append((ts, frame[0, 0].tolist()))

    def measure(model, *, hr_window, hrv_window):
        assert model.closed
        assert hr_window is hrv_window is None
        return {
            "hr": 74.25,
            "SQI": 0.8,
            "hrv_status": "available",
            "respiration_status": "available",
            "hrv_signal_quality": 0.8,
            "hrv_window_seconds": 35.0,
            "breathing_rate_per_min": 15.0,
            "hrv_summary": {
                "nn50": 4,
                "rr_interval_count": 28,
                "rmssd_ms": 33.0,
                "sdnn_ms": 43.0,
                "pnn50_percent": 17.5,
                "lf_hf_ratio": 1.5,
            },
        }

    monkeypatch.setattr(upstream, "measure_vitals", measure)
    reader = OpenRppgReader(Model)
    frames = source([(80.0, np.array([[[1, 2, 3]]], dtype=np.uint8)),
                     (80.0625, np.array([[[4, 5, 6]]], dtype=np.uint8))])
    left = reader.result(frames)
    right = reader.result(frames)
    assert len(models) == 2 and models[0] is not models[1]
    assert all(model.frames == [(0.0, [3, 2, 1]), (0.0625, [6, 5, 4])] for model in models)
    assert left == right
    assert (left.heart_rate_bpm, left.sqi, left.respiration_rate_per_min) == (74.25, 0.8, 15.0)
    assert (left.hrv.rmssd_ms, left.hrv.sdnn_ms, left.hrv.pnn50_percent, left.hrv.lf_hf_ratio) == (
        33.0, 43.0, 17.5, 1.5,
    )
    assert left.hrv.nn50 == 4 and left.hrv.rr_interval_count == 28
    assert left.face_coverage is None
    assert left.diagnostics.window_seconds == 35.0


def test_inference_failure_exits_upstream_session_and_propagates():
    failure = RuntimeError("damaged model")
    exited = []

    class Model:
        def __enter__(self):
            return self

        def __exit__(self, error_type, error, traceback):
            exited.append(error)

        def update_frame(self, frame, ts):
            raise failure

    video = source([(100.0, np.zeros((64, 48, 3), dtype=np.uint8))])
    with pytest.raises(RuntimeError, match="damaged model") as caught:
        OpenRppgReader(Model).result(video)
    assert caught.value is failure
    assert exited == [failure]


@pytest.mark.parametrize(("failure", "expected"), [
    (FileNotFoundError("missing weights"), FileNotFoundError),
    (RuntimeError("invalid model"), RuntimeError),
])
def test_model_loading_propagates_original_failure(failure, expected):
    def fail():
        raise failure

    video = source([(100.0, np.zeros((4, 4, 3), dtype=np.uint8))])
    with pytest.raises(expected) as caught:
        OpenRppgReader(fail).result(video)
    assert caught.value is failure
