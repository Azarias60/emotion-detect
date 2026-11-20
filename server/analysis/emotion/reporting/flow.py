"""Emotion analysis orchestration; the caller owns admission."""

import asyncio
import re
from collections.abc import Callable
from dataclasses import dataclass

from server.analysis.emotion.facts import expression_fact
from server.analysis.emotion.reporting.types import (
    EmotionMeasurements,
    EmotionReportFacts,
    EmotionReportInput,
    EmotionReportPort,
    EmotionReportResult,
)
from server.analysis.facts import (
    behavior_observation_fact,
    fatigue_advice_fact,
    fatigue_assessment_fact,
    speech_fact,
    video_physiology_fact,
)
from server.analysis.video import assess_behavior, measure_video_physiology, observe_behavior, physiology_diagnostics
from server.capture.types import DetectionMedia
from server.compute.expression.types import ExpressionPort
from server.compute.facephys.types import VideoPhysiologyReader
from server.compute.fatigue.types import BehaviorPort, FatigueAdvicePort, FatigueScoringPort
from server.compute.speech.service import ASR_SAMPLE_RATE
from server.compute.speech.types import AudioData, SpeechPort
from server.concurrency import gather_and_cancel_siblings, run_blocking
from server.detection_errors import DetectionInputError


@dataclass(frozen=True)
class EmotionAnalysisFlowService:
    expression: ExpressionPort
    behavior: BehaviorPort
    facephys_factory: Callable[[], VideoPhysiologyReader]
    speech: SpeechPort
    emotion_report: EmotionReportPort
    fatigue_scoring: FatigueScoringPort
    fatigue_advice: FatigueAdvicePort

    async def measure(self, inputs: EmotionReportInput, media: DetectionMedia) -> EmotionMeasurements:
        """Consume capture-pool data and drain all work before returning or raising.

        Normal missing observations remain None. Technical errors propagate to
        the caller; the text capability rejects empty upstream reports.
        """
        reader = self.facephys_factory()
        window = media.video_window()
        frames_factory = (lambda: (f.data for f in window)) if window is not None else None
        audio_window = media.audio_window(sample_rate=ASR_SAMPLE_RATE)
        audio = (
            AudioData(pcm=audio_window.pcm, sample_rate=audio_window.sample_rate)
            if audio_window else None
        )
        if window is None and audio is None:
            raise DetectionInputError("全部检测数据缺失，请先采集音视频")

        async def fetch_expression():
            return await run_blocking(
                self.expression.result_frames, frames_factory,
            ) if frames_factory else None

        async def fetch_speech():
            return await run_blocking(self.speech.result, audio) if audio else None

        expression, behavior, physiology, speech = await gather_and_cancel_siblings(
            asyncio.create_task(fetch_expression()),
            asyncio.create_task(observe_behavior(self.behavior, window)),
            asyncio.create_task(measure_video_physiology(reader, window)),
            asyncio.create_task(fetch_speech()),
        )
        measured = video_physiology_fact(physiology)
        assessment, advice = assess_behavior(behavior, self.fatigue_scoring, self.fatigue_advice)
        if all(value is None for value in (
            expression, behavior, measured, speech,
        )):
            raise DetectionInputError("缺少可用于生成情绪报告的观察数据")
        return EmotionMeasurements(
            expression=expression, behavior=behavior, fatigue=assessment, advice=advice,
            speech=speech, physiology=measured, answers=inputs.answers,
            detection_id=inputs.detection_id, diagnostics=physiology_diagnostics(physiology, window),
        )

    async def write(self, measured: EmotionMeasurements) -> EmotionReportResult:
        facts = EmotionReportFacts(
            expression=expression_fact(measured.expression),
            behavior=behavior_observation_fact(measured.behavior),
            fatigue=fatigue_assessment_fact(measured.fatigue),
            advice=fatigue_advice_fact(measured.advice),
            answers=measured.answers,
            speech=speech_fact(measured.speech),
            physiology=measured.physiology,
        )
        report = await run_blocking(self.emotion_report.result, facts)
        return EmotionReportResult(
            expression=measured.expression, behavior=measured.behavior, fatigue=measured.fatigue,
            advice=measured.advice, speech=measured.speech, physiology=measured.physiology,
            answers=measured.answers, detection_id=measured.detection_id, diagnostics=measured.diagnostics,
            report=report, word_count=extract_emotion_count_from_report(report.text),
        )


def extract_emotion_count_from_report(report: str) -> dict[str, int]:
    pattern = r'喜怒哀虑统计：喜:(\d+) 怒:(\d+) 哀:(\d+) 虑:(\d+)'
    match = re.search(pattern, report)
    if match:
        return {"喜": int(match.group(1)), "怒": int(match.group(2)), "哀": int(match.group(3)), "虑": int(match.group(4))}
    return {"喜": 0, "怒": 0, "哀": 0, "虑": 0}
