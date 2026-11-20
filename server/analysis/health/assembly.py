"""Health analysis composition; imported only by the assembly root.

The domain owns no resident resources — no model slots, locks or connections;
the LLM client is created per call inside the adapters, so there is no close
action to register. All adapter wiring lives here.
"""

from server.analysis.health.observation import HealthObservationService
from server.analysis.health.reporting.flow import HealthAnalysisFlowService
from server.analysis.health.questions import prepare_health_questions
from server.analysis.health.reporting.text import (
    HealthReportTextPort,
    OpenAIHealthReportTextReader,
)
from server.analysis.llm_client import create_client


def create_health_analysis_flow_service(
    *, behavior, facephys_factory, speech, fatigue_scoring, fatigue_advice,
) -> HealthAnalysisFlowService:
    """Compose the health flow service with its LLM report port bound."""
    return HealthAnalysisFlowService(
        behavior=behavior, facephys_factory=facephys_factory,
        speech=speech,
        fatigue_scoring=fatigue_scoring, fatigue_advice=fatigue_advice,
        health_report=create_health_report_port(),
    )


def create_health_report_port() -> HealthReportTextPort:
    """Bind the shared LLM transport to the health report text port."""
    return HealthReportTextPort(OpenAIHealthReportTextReader(create_client))


def create_health_observation_service(*, capture, tongue) -> HealthObservationService:
    return HealthObservationService(
        capture=capture, tongue=tongue, health_questions=prepare_health_questions,
    )
