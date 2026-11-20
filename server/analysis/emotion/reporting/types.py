"""Emotion report inputs, facts, results and ports."""

from dataclasses import dataclass, field
from typing import Protocol

import server.analysis.facts as shared_facts
from server.analysis.emotion.facts import ExpressionFact
from server.capture.types import DetectionMedia
from server.compute.facephys.types import PhysiologyDiagnostics
from server.compute.fatigue.types import BehaviorObservation, FatigueAdvice, FatigueAssessment
from server.compute.expression.types import ExpressionObservations
from server.compute.speech.types import Transcript


@dataclass(frozen=True)
class EmotionReportText:
    text: str


@dataclass(frozen=True)
class EmotionAnswer:
    """One settled per-question answer captured during the emotion session."""

    question: str
    answer: str


@dataclass(frozen=True)
class EmotionReportFacts:
    """Emotion report fact inputs; required advice, optional observations.

    Settled per-question answers and whole-audio speech stay separate facts.
    """

    expression: ExpressionFact | None
    behavior: shared_facts.BehaviorObservationFact | None
    fatigue: shared_facts.FatigueAssessmentFact | None
    advice: shared_facts.FatigueAdviceFact
    answers: tuple[EmotionAnswer, ...]
    speech: shared_facts.SpeechFact | None
    physiology: shared_facts.VideoPhysiologyFact | None


@dataclass(frozen=True)
class EmotionReportInput:
    """Identity of the finished recording plus its settled answer snapshot."""

    detection_id: str = ""
    answers: tuple[EmotionAnswer, ...] = ()


@dataclass(frozen=True)
class EmotionMeasurements:
    expression: ExpressionObservations | None
    behavior: BehaviorObservation | None
    fatigue: FatigueAssessment | None
    advice: FatigueAdvice
    speech: Transcript | None
    physiology: shared_facts.VideoPhysiologyFact | None
    detection_id: str
    answers: tuple[EmotionAnswer, ...] = field(default=(), kw_only=True)
    diagnostics: PhysiologyDiagnostics | None = field(default=None, kw_only=True)


@dataclass(frozen=True)
class EmotionReportResult(EmotionMeasurements):
    report: EmotionReportText
    word_count: dict[str, int]


class EmotionReportFlowPort(Protocol):
    """Generate a report from parsed input and sealed capture evidence.

    Missing facts raise DetectionInputError; capability failures and cancellation
    propagate after actual work settles. Admission belongs to the caller.
    """

    async def measure(self, inputs: EmotionReportInput, media: DetectionMedia) -> EmotionMeasurements: ...

    async def write(self, measured: EmotionMeasurements) -> EmotionReportResult: ...


class EmotionReportPort(Protocol):
    def result(self, facts: EmotionReportFacts) -> EmotionReportText: ...
