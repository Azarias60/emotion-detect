"""Sealed-video policy uses upstream HR and PRV, including its units."""

from pathlib import Path

import numpy as np
import pytest

from server.compute.facephys.vitals import measure_vitals

# Upstream rppg integration and real FacePhys inference are not part of fast PR CI.
pytestmark = pytest.mark.model


@pytest.fixture
def upstream():
    from rppg import main

    return main


def signal_model(upstream, seconds, *, flat=False):
    class SignalModel:
        has_signal = True
        fps = 30
        latency = 0
        hr = upstream.Model.hr

        def __init__(self):
            self.timestamps = np.arange(seconds * self.fps) / self.fps
            t = self.timestamps
            self.signal = np.zeros_like(t) if flat else np.sin(
                2 * np.pi * 1.25 * t + 0.3 * np.sin(2 * np.pi * 0.25 * t),
            )
            self.now = t[-1]

        def bvp(self, start=0, end=None, raw=False):
            first = np.searchsorted(self.timestamps, self.now + start, side="left") if start < 0 else 0
            return self.signal[first:], self.timestamps[first:]

    return SignalModel()


def test_flat_signal_does_not_yield_an_upstream_spectral_peak(upstream):
    result = measure_vitals(signal_model(upstream, 35, flat=True), hr_window=None)
    assert result["hr"] is None
    assert result["hrv_status"] == "no_signal"


def test_short_high_quality_signal_still_yields_upstream_hr(upstream):
    model = signal_model(upstream, 12)
    result = measure_vitals(model, hr_window=None)
    assert result["hr"] == model.hr(return_hrv=False)["hr"]
    assert result["SQI"] > 0.5
    assert result["hrv_status"] == "collecting"
    assert result["breathing_rate_per_min"] is None


def test_low_quality_retains_the_measured_window_and_available_heart_rate(upstream):
    model = signal_model(upstream, 35)
    def low_quality(*args, **kwargs):
        return {"hr": 74.0, "SQI": 0.4, "hrv": {}}
    model.hr = low_quality
    result = measure_vitals(model, hr_window=None, hrv_window=None)
    assert result["hr"] == 74.0
    assert result["hrv_status"] == result["respiration_status"] == "low_signal_quality"
    assert result["hrv_window_seconds"] == model.now
    assert result["breathing_rate_per_min"] is None


def test_prv_failure_preserves_the_measurable_heart_rate(upstream, monkeypatch):
    def fail_prv(*args, **kwargs):
        raise ValueError("insufficient pulse intervals")

    model = signal_model(upstream, 35)
    expected_hr = model.hr(return_hrv=False)["hr"]
    monkeypatch.setattr(upstream, "get_prv", fail_prv)

    result = measure_vitals(model, hr_window=None, hrv_window=None)

    assert result["hr"] == expected_hr
    assert result["hrv"] == {}
    assert result["hrv_status"] == result["respiration_status"] == "unavailable"
    assert result["breathing_rate_per_min"] is None


def test_full_video_returns_upstream_prv_with_reference_display_units(upstream):
    model = signal_model(upstream, 35)
    result = measure_vitals(model, hr_window=None, hrv_window=None)
    assert result["hrv_window_seconds"] == model.now
    signal, timestamps = model.bvp()
    expected = upstream.get_prv(signal, timestamps, sr=model.fps)
    assert result["hrv_status"] == result["respiration_status"] == "available"
    assert result["hrv"] == expected
    assert result["hrv_summary"]["rmssd_ms"] == expected["rmssd"]
    assert result["hrv_summary"]["sdnn_ms"] == expected["sdnn"]
    assert result["hrv_summary"]["pnn50_percent"] == expected["pnn50"] * 100
    assert result["hrv_summary"]["lf_hf_ratio"] == expected["LF/HF"]
    assert result["breathing_rate_per_min"] == expected["breathingrate"] * 60

    import heartpy
    working, _ = heartpy.process(signal, model.fps, high_precision=True, clean_rr=True)
    assert result["hrv_summary"]["nn50"] == len(working["nn50"])
    assert result["hrv_summary"]["rr_interval_count"] == len(working["RR_list_cor"])


def test_real_facephys_consumes_timestamped_capture_frames(upstream):
    import cv2
    from matplotlib import get_data_path

    from server.bootstrap import assemble
    from server.compute.facephys.types import VideoAnalysisInput

    photo = Path(get_data_path()) / "sample_data" / "grace_hopper.jpg"
    frame = cv2.resize(cv2.imread(str(photo)), (300, 350))

    def frames():
        for index in range(120):
            image = frame.copy()
            green = image[:, :, 1].astype(float)
            image[:, :, 1] = np.clip(green + 3 * np.sin(2 * np.pi * 1.25 * index / 30), 0, 255)
            yield 100.0 + index / 30, image

    service = assemble().facephys
    service.load()
    try:
        result = service.create_reader().result(VideoAnalysisInput(frames))
    finally:
        service.close()
    assert result is not None
    assert 30 < result.heart_rate_bpm < 180
    assert result.sqi is not None
    assert result.hrv is None
