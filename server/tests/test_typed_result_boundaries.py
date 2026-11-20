"""Typed business contracts replace dictionaries at capability boundaries.

These tests pin the capture summary types, the health-report result values,
and the internal immutability of frozen capture summaries. Hardware summaries
are not projected through the Gateway.
"""

from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from server.capture.domain import HeartRateWindow, TemperatureWindow

SERVER_DIR = Path(__file__).resolve().parents[1]

BUSINESS_MODULES = (
    "capture/domain.py",
    "capture/types/reads.py",
    "analysis/llm_client.py",
    "analysis/health/assembly.py",
    "analysis/health/reporting/flow.py",
    "analysis/health/questions.py",
    "analysis/facts.py",
    "analysis/video.py",
    "analysis/health/reporting/text.py",
    "analysis/health/reporting/types.py",
    "analysis/health/observation.py",
    "analysis/health/types.py",
    "analysis/health/facts.py",
    "analysis/emotion/facts.py",
    "analysis/emotion/types.py",
    "analysis/fatigue/observation.py",
    "analysis/fatigue/types.py",
    "analysis/fatigue/reporting/flow.py",
    "analysis/fatigue/reporting/types.py",
    "analysis/emotion/assembly.py",
    "analysis/emotion/reporting/flow.py",
    "analysis/emotion/questions.py",
    "analysis/emotion/reporting/text.py",
    "analysis/emotion/reporting/types.py",
    "compute/speech/service.py",
    "compute/tongue/constitution/adapters/json_rules.py",
    "compute/tongue/tongue_quality/adapters/opencv.py",
    "presentation/gateway/serializers.py",
    "presentation/gateway/ports.py",
)

DELETED_MODULES = (
    "api/__init__.py",
    "api/emotion.py",
    "api/fatigue.py",
    "api/health.py",
    "tongue_assessment/__init__.py",
    "tongue_assessment/domain.py",
    "tongue_assessment/application.py",
    "tongue_assessment/contracts.py",
    "tongue_assessment/capture.py",
    "tongue_assessment/transport.py",
    "coordination/tongue_assessment.py",
    "emotion_detection/__init__.py",
    "emotion_detection/bootstrap.py",
    "emotion_detection/contracts.py",
    "emotion_detection/http.py",
    "emotion_detection/presenter.py",
    "fatigue_assessment/__init__.py",
    "fatigue_assessment/advice.py",
    "fatigue_assessment/assessment.py",
    "fatigue_assessment/bootstrap.py",
    "fatigue_assessment/contracts.py",
    "fatigue_assessment/domain.py",
    "fatigue_assessment/http.py",
    "fatigue_assessment/presenter.py",
    "health_consultation/__init__.py",
    "health_consultation/adapters/__init__.py",
    "health_consultation/adapters/openai.py",
    "health_consultation/application.py",
    "health_consultation/bootstrap.py",
    "health_consultation/contracts.py",
    "health_consultation/domain.py",
    "health_consultation/http.py",
    "health_consultation/ports.py",
)


# --- capture summary contracts ---------------------------------------------


def test_heart_rate_window_is_a_frozen_value_object():
    summary = HeartRateWindow(
        samples_bpm=(70.0, 72.5),
        average_bpm=71.0,
        latest_bpm=72.5,
        live_samples_bpm=(72.0,),
        source="ld6002_radar",
    )

    assert summary.samples_bpm == (70.0, 72.5)
    assert summary.source == "ld6002_radar"
    with pytest.raises(FrozenInstanceError):
        summary.average_bpm = 0.0


def test_temperature_window_is_a_frozen_value_object():
    summary = TemperatureWindow(
        ambient_temps=(24.5, 25.5),
        object_temps=(36.2,),
        avg_ambient_temp=25.0,
        avg_object_temp=36.2,
        latest_ambient_temp=25.5,
        latest_object_temp=36.2,
    )

    assert summary.object_temps == (36.2,)
    with pytest.raises(FrozenInstanceError):
        summary.avg_object_temp = 0.0


# --- health report result values -------------------------------------------


# --- Gateway serializer projections ----------------------------------------


def test_run_emotion_detection_is_absent_from_production_capabilities():
    hits = []
    for path in SERVER_DIR.rglob("*.py"):
        parts = set(path.relative_to(SERVER_DIR).parts)
        if {"tests", "__pycache__", ".venv", "site-packages"} & parts:
            continue
        if "run_emotion_detection" in path.read_text(encoding="utf-8"):
            hits.append(str(path.relative_to(SERVER_DIR)))
    assert hits == []

    for relative in DELETED_MODULES:
        assert not (SERVER_DIR / relative).exists(), relative
