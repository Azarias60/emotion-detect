"""Emotion analysis composition; imported only by the assembly root.

The domain owns no resident resources — no model slots, locks or connections;
the LLM client is created per call inside the adapters, so there is no close
action to register. All adapter wiring lives here.
"""

from server.analysis.emotion.reporting.flow import EmotionAnalysisFlowService
from server.analysis.emotion.questions import (
    EmotionQuestionPreparationService,
    LLMEmotionQuestionTextReader,
)
from server.analysis.emotion.reporting.text import OpenAIEmotionReportTextReader
from server.analysis.emotion.reporting.types import EmotionReportPort
from server.analysis.llm_client import create_client


def create_emotion_analysis_flow_service(
    *, expression, behavior, facephys_factory, speech, fatigue_scoring, fatigue_advice,
) -> EmotionAnalysisFlowService:
    """Compose the emotion flow service with its LLM report port bound."""
    return EmotionAnalysisFlowService(
        expression=expression, behavior=behavior,
        facephys_factory=facephys_factory, speech=speech,
        fatigue_scoring=fatigue_scoring, fatigue_advice=fatigue_advice,
        emotion_report=create_emotion_report_port(),
    )


def create_emotion_report_port() -> EmotionReportPort:
    """Bind the shared LLM transport to the emotion report capability port."""
    return OpenAIEmotionReportTextReader(create_client)


def create_emotion_question_service() -> EmotionQuestionPreparationService:
    """Emotion questions port; the capability owns the local-question bank."""
    return EmotionQuestionPreparationService(LLMEmotionQuestionTextReader(create_client))
