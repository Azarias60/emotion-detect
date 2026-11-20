"""FacePhys projection preserves measured values and missingness."""

import pytest

from server.analysis.facts import video_physiology_fact
from server.compute.facephys.types import VideoHrvMeasurement, VideoPhysiologyMeasurement


@pytest.mark.parametrize("measurement", [None, VideoPhysiologyMeasurement(sqi=0.8)])
def test_no_measurements_is_absent_even_with_quality_metadata(measurement):
    assert video_physiology_fact(measurement) is None


def test_single_pass_projects_partial_values_and_rr_provenance():
    result = video_physiology_fact(VideoPhysiologyMeasurement(
        respiration_rate_per_min=15.2,
        hrv=VideoHrvMeasurement(
            sdnn_ms=41.2, rmssd_ms=33.8, nn50=6, pnn50_percent=18.4,
            lf_hf_ratio=1.7, rr_interval_count=3,
        ),
    ))
    assert result == {
        "heart_rate_bpm": None,
        "respiration_rate_per_min": 15.2,
        "hrv": {
            "sdnn_ms": 41.2, "rmssd_ms": 33.8, "nn50": 6, "pnn50_percent": 18.4,
            "lf_hf_ratio": 1.7, "rr_interval_count": 3, "rr_source": "rppg_peak_derived",
        },
    }
