"""Typed result and entry point for the fatigue report."""

from dataclasses import dataclass
from typing import Protocol

from server.analysis.facts import VideoPhysiologyFact
from server.capture.types import DetectionMedia
from server.compute.facephys.types import PhysiologyDiagnostics
from server.compute.fatigue.types import BehaviorObservation, FatigueAdvice, FatigueAssessment


@dataclass(frozen=True)
class FatigueReportResult:
    behavior: BehaviorObservation
    fatigue: FatigueAssessment
    advice: FatigueAdvice
    physiology: VideoPhysiologyFact | None
    measurement_notice: str | None
    diagnostics: PhysiologyDiagnostics | None = None


class FatigueReportFlowPort(Protocol):
    async def run(self, media: DetectionMedia) -> FatigueReportResult: ...
