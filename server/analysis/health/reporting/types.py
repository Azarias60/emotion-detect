"""Health report inputs, facts, results and ports."""

from dataclasses import dataclass, field
from typing import Protocol

import server.analysis.facts as shared_facts
from server.analysis.health.facts import (
    HealthAnswer,
    TongueQualityFact,
    TongueRecognitionFact,
    ConstitutionAssessmentFact,
    ConstitutionReportFact,
)
from server.capture.types import DetectionMedia
from server.compute.facephys.types import PhysiologyDiagnostics
from server.compute.fatigue.types import BehaviorObservation, FatigueAdvice, FatigueAssessment
from server.compute.speech.types import Transcript
from server.compute.tongue.types import (
    ConstitutionAssessment,
    ConstitutionReport,
    TongueQuality,
    TongueRecognition,
)


@dataclass(frozen=True)
class HealthReportText:
    text: str


@dataclass(frozen=True)
class HealthReportFacts:
    """Health report fact inputs; submitted tongue values, settled answers and
    whole-audio speech stay separate facts."""

    tongue_quality: TongueQualityFact | None
    tongue_recognition: TongueRecognitionFact | None
    constitution: ConstitutionAssessmentFact | None
    constitution_report: ConstitutionReportFact | None
    answers: tuple[HealthAnswer, ...]
    behavior: shared_facts.BehaviorObservationFact | None
    fatigue: shared_facts.FatigueAssessmentFact | None
    advice: shared_facts.FatigueAdviceFact
    speech: shared_facts.SpeechFact | None
    physiology: shared_facts.VideoPhysiologyFact | None


@dataclass(frozen=True)
class HealthReportInput:
    """Submitted tongue facts plus the settled per-question answer snapshot.

    Answers arrive already transcribed by the detection session; the report
    flow never re-slices question audio.
    """

    tongue_quality: TongueQuality | None = None
    tongue_recognition: TongueRecognition | None = None
    constitution: ConstitutionAssessment | None = None
    constitution_report: ConstitutionReport | None = None
    answers: tuple[HealthAnswer, ...] = ()

    detection_id: str = ""


@dataclass(frozen=True)
class HealthMeasurements:
    behavior: BehaviorObservation | None
    fatigue: FatigueAssessment | None
    advice: FatigueAdvice
    speech: Transcript | None
    physiology: shared_facts.VideoPhysiologyFact | None
    tongue_quality: TongueQuality | None
    tongue_recognition: TongueRecognition | None
    constitution: ConstitutionAssessment | None
    constitution_report: ConstitutionReport | None
    answers: tuple[HealthAnswer, ...]
    detection_id: str
    diagnostics: PhysiologyDiagnostics | None = field(default=None, kw_only=True)


@dataclass(frozen=True)
class HealthReportResult(HealthMeasurements):
    report: HealthReportText


class HealthReportFlowPort(Protocol):
    """Generate a report from parsed input and sealed capture evidence.

    Missing facts/answers raise DetectionInputError; capability failures and
    cancellation propagate after work settles. Admission belongs to the caller.
    """

    async def measure(self, inputs: HealthReportInput, media: DetectionMedia) -> HealthMeasurements: ...

    async def write(self, measured: HealthMeasurements) -> HealthReportResult: ...


class HealthReportPort(Protocol):
    def result(self, facts: HealthReportFacts) -> HealthReportText: ...
