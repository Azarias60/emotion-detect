"""FacePhys 测量结果类型的数据不变量契约。"""
from dataclasses import fields

import pytest

from server.compute.facephys.types import (
    VideoHrvMeasurement,
    VideoPhysiologyMeasurement,
)


def test_measurement_results_expose_only_capability_fields():
    assert {field.name for field in fields(VideoPhysiologyMeasurement)} == {
        "heart_rate_bpm", "respiration_rate_per_min", "hrv", "sqi",
        "face_coverage", "source", "diagnostics",
    }
    assert {field.name for field in fields(VideoHrvMeasurement)} == {
        "rmssd_ms", "sdnn_ms", "nn50", "pnn50_percent", "lf_hf_ratio",
        "rr_interval_count",
    }


def test_absent_capabilities_stay_none_without_status_labels():
    missing = VideoPhysiologyMeasurement()
    assert missing.heart_rate_bpm is None
    assert missing.respiration_rate_per_min is None
    assert missing.hrv is None
    assert missing.sqi is None
    assert missing.face_coverage is None
    assert missing.source == "video"


@pytest.mark.parametrize("kwargs", [
    {"heart_rate_bpm": float("nan")},
    {"heart_rate_bpm": -10.0},
    {"respiration_rate_per_min": float("inf")},
    {"respiration_rate_per_min": 0.0},
    {"sqi": 1.5},
    {"sqi": -0.1},
    {"face_coverage": 2.0},
])
def test_out_of_domain_values_are_rejected(kwargs):
    with pytest.raises(ValueError):
        VideoPhysiologyMeasurement(**kwargs)


def test_hrv_indices_reject_explicit_zero_rr_intervals():
    with pytest.raises(ValueError, match="require RR"):
        VideoHrvMeasurement(rmssd_ms=30.0, rr_interval_count=0)


@pytest.mark.parametrize("kwargs", [
    {"rmssd_ms": float("nan")},
    {"sdnn_ms": float("inf")},
    {"nn50": -1},
    {"rr_interval_count": -3},
])
def test_hrv_rejects_nonfinite_and_negative_counts(kwargs):
    with pytest.raises(ValueError):
        VideoHrvMeasurement(**kwargs)


def test_hrv_accepts_full_indices_with_rr_count():
    value = VideoHrvMeasurement(
        rmssd_ms=33.8, sdnn_ms=41.2, nn50=6, pnn50_percent=18.4,
        lf_hf_ratio=1.7, rr_interval_count=3,
    )
    assert value.lf_hf_ratio == 1.7
