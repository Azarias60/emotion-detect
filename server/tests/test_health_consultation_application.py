"""Health report use case and port contracts over health analysis facts."""

from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace

import pytest

from server.analysis.health.reporting.text import HealthReportTextPort
from server.analysis.health.reporting.text import OpenAIHealthReportTextReader
from server.analysis.health.facts import HealthAnswer
from server.analysis.health.reporting.types import (
    HealthReportFacts,
    HealthReportText,
)
from server.compute.fatigue.types import BehaviorObservation, FatigueAdvice
from server.compute.fatigue.types import FatigueAssessment
from server.compute.speech.types import Transcript, TranscriptSegment
from server.compute.tongue.types import (
    TongueDimensionResult,
    TongueQuality,
    TongueRecognition,
)


def _behavior():
    return BehaviorObservation(
        blink_count=10, blink_frequency=10.0, yawn_count=0, nod_count=0,
        nod_frequency=0.0, perclos_percent=0.0, speaking_percent=0.0,
        face_coverage=1.0,
    )


def _advice():
    return FatigueAdvice(level="轻度疲劳", text="减少连续用眼并安排休息；观察数据仅作参考。")


def _speech():
    return Transcript((TranscriptSegment("录音"),))


def _tongue_quality():
    return TongueQuality(True, (), 224, 224, 120.0, 40.0)


def _tongue_recognition():
    return TongueRecognition(
        crack=TongueDimensionResult("normal", 0.9),
        coated=TongueDimensionResult("white", 0.8),
        color=TongueDimensionResult("red", 0.9),
        indentation=TongueDimensionResult("indentation", 0.7),
    )


def _evidence(value):
    return asdict(value) if value is not None else None


def _facts(**overrides):
    values = dict(
        behavior=_evidence(_behavior()),
        fatigue=_evidence(FatigueAssessment(score=1, level="正常")),
        advice={"level": _advice().level, "text": _advice().text},
        speech={"segments": [{"text": segment.text} for segment in _speech().segments]},
        physiology=None,
        tongue_quality=_evidence(_tongue_quality()),
        tongue_recognition=_evidence(_tongue_recognition()),
        constitution=None, constitution_report=None,
        answers=[HealthAnswer("答案", "问题")],
    )
    values.update(overrides)
    return HealthReportFacts(**values)


def test_generated_report_rebuilds_payload_from_health_facts(monkeypatch):
    from server import settings

    monkeypatch.setitem(settings.AI_CONFIG, "api_key", "test-key")

    class Reader:
        def __init__(self):
            self.calls = []
        def generate(self, payload):
            self.calls.append(payload)
            return "原始报告\n"

    reader = Reader()
    facts = _facts()
    result = HealthReportTextPort(reader).result(facts)

    assert result == HealthReportText("原始报告\n")
    payload = reader.calls[0]
    assert payload["behavior"] == asdict(_behavior())
    assert payload["fatigue"] == {"score": 1, "level": "正常"}
    assert payload["advice"]["level"] == "轻度疲劳"
    assert payload["advice"]["text"]
    assert payload["speech"] == {"segments": [{"text": "录音"}]}
    assert payload["tongue_quality"] == asdict(_tongue_quality())
    assert payload["tongue_recognition"] == asdict(_tongue_recognition())
    assert payload["constitution"] is None and payload["constitution_report"] is None
    assert "舌象结果仅供健康参考" in payload["tongue_disclaimer"]
    assert payload["answers"] == [{"question": "问题", "answer": "答案"}]
    assert payload["physiology"] is None


def test_absent_values_stay_explicit_nulls_in_the_prompt(monkeypatch):
    from server import settings

    monkeypatch.setitem(settings.AI_CONFIG, "api_key", "test-key")

    class Reader:
        def generate(self, payload):
            self.payload = payload
            return "报告"

    reader = Reader()
    HealthReportTextPort(reader).result(_facts(
        behavior=None, fatigue=None, speech=None,
        tongue_quality=None, tongue_recognition=None,
        physiology=None,
    ))

    payload = reader.payload
    assert payload["behavior"] is None
    assert payload["fatigue"] is None
    assert payload["speech"] is None
    assert payload["tongue_quality"] is None
    assert payload["tongue_recognition"] is None
    assert payload["physiology"] is None


def test_reader_exception_propagates_through_the_port_boundary(monkeypatch):
    from server import settings

    monkeypatch.setitem(settings.AI_CONFIG, "api_key", "test-key")

    class Reader:
        def generate(self, payload):
            raise RuntimeError("unavailable")

    with pytest.raises(RuntimeError, match="unavailable"):
        HealthReportTextPort(Reader()).result(_facts())


def test_health_types_have_no_framework_dependencies():
    import server.analysis.health.reporting.types as contracts

    source = Path(contracts.__file__).read_text()
    assert "fastapi" not in source.lower()
    assert "openai" not in source.lower()


def test_health_report_port_preserves_generated_text(monkeypatch):
    from server import settings

    monkeypatch.setitem(settings.AI_CONFIG, "api_key", "test-key")
    response = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="生成的报告"))])
    client = SimpleNamespace(chat=SimpleNamespace(
        completions=SimpleNamespace(create=lambda **kwargs: response),
    ))

    port = HealthReportTextPort(OpenAIHealthReportTextReader(lambda: client))
    result = port.result(_facts())

    assert result == HealthReportText("生成的报告")


def test_health_report_port_propagates_model_failure(monkeypatch):
    from server import settings

    monkeypatch.setitem(settings.AI_CONFIG, "api_key", "test-key")

    def broken_client():
        raise RuntimeError("模型不可达")

    with pytest.raises(RuntimeError, match="模型不可达") as error:
        HealthReportTextPort(OpenAIHealthReportTextReader(broken_client)).result(_facts())
    assert error.value.__cause__ is None
