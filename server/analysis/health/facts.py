"""Health facts shared by observation, question preparation and reporting."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, NotRequired

from typing_extensions import TypedDict

from server.compute.tongue.types import (
    ConstitutionAssessment, ConstitutionCandidate, ConstitutionReport,
    TongueDimensionResult, TongueQuality, TongueRecognition,
)


@dataclass(frozen=True)
class HealthAnswer:
    """One settled answer transcribed by the detection session."""

    answer: str = ""
    question: str | None = None


class HealthAnswerFact(TypedDict):
    answer: str
    question: NotRequired[str | None]


def health_answer_fact(value: HealthAnswer) -> HealthAnswerFact:
    if value.question is None:
        return {"answer": value.answer}
    return {"question": value.question, "answer": value.answer}


class TongueQualityFact(TypedDict):
    passed: bool
    warnings: tuple[str, ...]
    width: int
    height: int
    brightness: float
    sharpness: float


class TongueRecognitionFact(TypedDict):
    crack: TongueDimensionResultFact
    coated: TongueDimensionResultFact
    color: TongueDimensionResultFact
    indentation: TongueDimensionResultFact


class TongueDimensionResultFact(TypedDict):
    class_name: str
    probability: float


class LabeledTongueDimensionFact(TongueDimensionResultFact):
    label: str


class ConstitutionAssessmentFact(TypedDict):
    candidates: tuple[ConstitutionCandidateFact, ...]


class ConstitutionCandidateFact(TypedDict):
    id: int
    name: str
    english: str
    weight: float
    category: str


class ConstitutionReportFact(TypedDict):
    constitution_id: int
    constitution_name: str
    constitution_english: str
    category: str
    one_line: str
    body_signals: tuple[str, ...]
    tongue: str
    mind_summary: str
    mind_highlights: tuple[str, ...]
    disease_risk: tuple[str, ...]
    environment_note: str
    advice: Mapping[str, object]


def tongue_quality_fact(value: TongueQuality | None) -> TongueQualityFact | None:
    if value is None:
        return None
    return {
        'passed': value.passed,
        'warnings': value.warnings,
        'width': value.width,
        'height': value.height,
        'brightness': value.brightness,
        'sharpness': value.sharpness,
    }


def tongue_recognition_fact(value: TongueRecognition | None) -> TongueRecognitionFact | None:
    if value is None:
        return None
    return {
        'crack': tongue_dimension_result_fact(value.crack),
        'coated': tongue_dimension_result_fact(value.coated),
        'color': tongue_dimension_result_fact(value.color),
        'indentation': tongue_dimension_result_fact(value.indentation),
    }


def tongue_dimension_result_fact(value: TongueDimensionResult) -> TongueDimensionResultFact:
    return {
        'class_name': value.class_name,
        'probability': value.probability,
    }


def constitution_assessment_fact(value: ConstitutionAssessment | None) -> ConstitutionAssessmentFact | None:
    if value is None:
        return None
    return {
        'candidates': tuple(constitution_candidate_fact(item) for item in value.candidates),
    }


def constitution_candidate_fact(value: ConstitutionCandidate) -> ConstitutionCandidateFact:
    return {
        'id': value.id,
        'name': value.name,
        'english': value.english,
        'weight': value.weight,
        'category': value.category,
    }


def constitution_report_fact(value: ConstitutionReport | None) -> ConstitutionReportFact | None:
    if value is None:
        return None
    return {
        'constitution_id': value.constitution_id,
        'constitution_name': value.constitution_name,
        'constitution_english': value.constitution_english,
        'category': value.category,
        'one_line': value.one_line,
        'body_signals': value.body_signals,
        'tongue': value.tongue,
        'mind_summary': value.mind_summary,
        'mind_highlights': value.mind_highlights,
        'disease_risk': value.disease_risk,
        'environment_note': value.environment_note,
        'advice': value.advice,
    }


_TONGUE_LABELS = {
    "color": {"red": "淡红舌", "white": "淡白舌", "crimson": "深红舌"},
    "coated": {"white": "白苔", "yellow": "黄苔", "nocoated": "无苔"},
    "crack": {"crack": "裂纹舌", "normal": "无裂纹"},
    "indentation": {"indentation": "齿痕舌", "normal": "无齿痕"},
}


def labeled_tongue_recognition(
    value: TongueRecognition | None,
) -> dict[str, LabeledTongueDimensionFact] | None:
    """Add the shared Chinese vocabulary to the four recognized dimensions."""
    fact = tongue_recognition_fact(value)
    if fact is None:
        return None
    return {
        dimension: {**item, "label": _TONGUE_LABELS[dimension].get(item["class_name"], item["class_name"])}
        for dimension, item in fact.items()
    }
