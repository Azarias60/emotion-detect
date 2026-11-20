"""Upstream extensions must not silently change report prompt inputs."""

from dataclasses import dataclass
from importlib import import_module
from typing import get_args, get_type_hints

import pytest
from pydantic import TypeAdapter, ValidationError

from server.analysis import facts as shared_facts
from server.analysis.emotion.reporting.types import EmotionReportFacts
from server.analysis.emotion.facts import ExpressionFact, expression_fact
from server.analysis.health.reporting.types import HealthReportFacts
from server.compute.expression.types import ExpressionObservation, ExpressionObservations
from server.compute.fatigue.types import BehaviorObservation, FatigueAdvice, FatigueAssessment
from server.compute.speech.types import Transcript, TranscriptSegment
from server.presentation.gateway import serializers as wire
from server.presentation.gateway import schemas as wire_schemas
from server.detection.flows import fatigue_facts
from server.analysis.fatigue.reporting.types import FatigueReportResult
from server.compute.facephys.types import PhysiologyDiagnostics


def test_emotion_report_and_http_project_the_same_expression_fields():
    @dataclass(frozen=True)
    class ExtendedObservation(ExpressionObservation):
        experimental: str = "unpublished"

    observations = ExpressionObservations(
        observations=(ExtendedObservation("happy", .9, 1., 4),),
        class_counts=(("happy", 1),),
    )
    assert expression_fact(observations) == {
        "observations": ({"label": "happy", "confidence": .9, "timestamp": 1., "class_index": 4},),
        "class_counts": (("happy", 1),),
    }
    assert wire.serialize_expression_result(observations) == {
        "observations": [{"label": "happy", "confidence": .9, "timestamp": 1., "class_index": 4}],
        "class_counts": [["happy", 1]],
    }


@pytest.mark.parametrize("domain", ("emotion", "health"))
def test_shared_fact_types_have_no_legacy_domain_reexports(domain):
    domain_types = import_module(f"server.analysis.{domain}.facts")
    for name in (
        "BehaviorObservationFact", "FatigueAssessmentFact", "FatigueAdviceFact",
        "HRVMeasurementFact", "VideoPhysiologyFact",
        "SpeechFact", "SpeechSegmentFact",
    ):
        assert name not in vars(domain_types), name
    for name in (
        "behavior_observation_fact", "fatigue_assessment_fact", "fatigue_advice_fact",
        "hrv_measurement_fact", "video_physiology_fact",
    ):
        assert name not in vars(domain_types), name


@pytest.mark.parametrize("report_type", (EmotionReportFacts, HealthReportFacts))
def test_report_types_reference_canonical_shared_contracts(report_type):
    hints = get_type_hints(report_type)
    assert shared_facts.BehaviorObservationFact in get_args(hints["behavior"])
    assert shared_facts.FatigueAssessmentFact in get_args(hints["fatigue"])
    assert hints["advice"] is shared_facts.FatigueAdviceFact
    assert shared_facts.SpeechFact in get_args(hints["speech"])
    assert shared_facts.VideoPhysiologyFact in get_args(hints["physiology"])


def test_fatigue_observation_uses_single_explicit_projection_for_report_and_http():
    @dataclass(frozen=True)
    class ExtendedBehavior(BehaviorObservation):
        unpublished_debug: str = "do-not-leak"

    observation = ExtendedBehavior(
        blink_count=9, blink_frequency=10.0, yawn_count=1,
        nod_count=2, nod_frequency=3.0, perclos_percent=15.0,
        speaking_percent=20.0, face_coverage=0.9,
    )
    fact = shared_facts.behavior_observation_fact(observation)
    assert set(fact) == {
        "blink_count", "blink_frequency", "yawn_count", "nod_count",
        "nod_frequency", "perclos_percent", "speaking_percent", "face_coverage",
    }
    assert wire.serialize_behavior_result(observation) == fact
    assert "unpublished_debug" not in wire.serialize_behavior_result(observation)


def test_fatigue_facts_add_only_the_normalized_score_and_share_advice():
    """疲劳分数的 HTTP 专有字段收口在疲劳事实投影；建议与共享事实同形。"""
    assessment = FatigueAssessment(score=3, level="中度疲劳")
    advice = FatigueAdvice(level="注意休息", text="休息片刻")
    behavior = BehaviorObservation(
        blink_count=1, blink_frequency=1.0, yawn_count=0, nod_count=0,
        nod_frequency=0.0, perclos_percent=0.0, speaking_percent=0.0,
        face_coverage=1.0,
    )
    measured = FatigueReportResult(
        behavior=behavior, fatigue=assessment, advice=advice,
        physiology=None, measurement_notice=None,
        diagnostics=PhysiologyDiagnostics("no_signal", "no_signal", None, 0.0),
    )
    facts = fatigue_facts(measured, detection_id=1)
    assert shared_facts.fatigue_assessment_fact(assessment) == {"score": 3, "level": "中度疲劳"}
    assert facts["fatigue"] == {
        **shared_facts.fatigue_assessment_fact(assessment), "score_fraction": 0.75,
    }
    assert facts["advice"] == shared_facts.fatigue_advice_fact(advice)


def test_speech_http_and_report_share_the_same_segment_projection():
    transcript = Transcript((TranscriptSegment("你好"), TranscriptSegment("世界")))
    expected = {"segments": [{"text": "你好"}, {"text": "世界"}]}
    assert shared_facts.speech_fact(transcript) == expected
    assert wire.serialize_speech_result(transcript) == expected
    assert wire.serialize_speech_result(None) is None


@pytest.mark.parametrize(("payload", "fact"), (
    (wire_schemas.ExpressionResult, ExpressionFact),
    (wire_schemas.BehaviorResult, shared_facts.BehaviorObservationFact),
    (wire_schemas.SpeechResult, shared_facts.SpeechFact),
))
def test_public_http_payload_reuses_canonical_field_types(payload, fact):
    assert get_type_hints(payload) == get_type_hints(fact)


def test_expression_payload_requires_a_label_count_pair():
    adapter = TypeAdapter(wire_schemas.ExpressionResult)
    adapter.validate_python({"observations": [], "class_counts": [["happy", 1]]})
    with pytest.raises(ValidationError, match="class_counts"):
        adapter.validate_python({"observations": [], "class_counts": [[1, "happy"]]})


def test_http_payload_validation_rejects_unpublished_fields():
    with pytest.raises(ValidationError, match="extra_forbidden"):
        TypeAdapter(wire_schemas.SpeechResult).validate_python({
            "segments": [{"text": "你好", "experimental": True}],
        })
