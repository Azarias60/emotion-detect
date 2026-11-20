"""Health analysis orchestration; the caller owns admission."""

import asyncio
from collections.abc import Callable
from dataclasses import dataclass

from server.analysis.facts import (
    behavior_observation_fact,
    fatigue_advice_fact,
    fatigue_assessment_fact,
    speech_fact,
    video_physiology_fact,
)
from server.analysis.health.facts import (
    constitution_assessment_fact,
    constitution_report_fact,
    tongue_quality_fact,
    tongue_recognition_fact,
)

from server.analysis.health.reporting.types import (
    HealthMeasurements,
    HealthReportFacts,
    HealthReportInput,
    HealthReportPort,
    HealthReportResult,
)
from server.analysis.video import assess_behavior, measure_video_physiology, observe_behavior, physiology_diagnostics
from server.capture.types import DetectionMedia
from server.compute.facephys.types import VideoPhysiologyReader
from server.compute.fatigue.types import BehaviorPort, FatigueAdvicePort, FatigueScoringPort
from server.compute.speech.service import ASR_SAMPLE_RATE
from server.compute.speech.types import AudioData, SpeechPort
from server.concurrency import gather_and_cancel_siblings, run_blocking
from server.detection_errors import DetectionInputError


@dataclass(frozen=True)
class HealthAnalysisFlowService:
    behavior: BehaviorPort
    facephys_factory: Callable[[], VideoPhysiologyReader]
    speech: SpeechPort
    health_report: HealthReportPort
    fatigue_scoring: FatigueScoringPort
    fatigue_advice: FatigueAdvicePort

    async def measure(self, inputs: HealthReportInput, media: DetectionMedia) -> HealthMeasurements:
        """Keep submitted facts and settled answers separate from whole-audio speech.

        Answers arrive already transcribed by the detection session; this flow
        never re-slices question audio. Normal missing observations remain
        None. Technical errors propagate after actual work settles; the text
        capability owns report validation.
        """
        reader = self.facephys_factory()
        window = media.video_window()
        audio_window = media.audio_window(sample_rate=ASR_SAMPLE_RATE)
        audio = (
            AudioData(pcm=audio_window.pcm, sample_rate=audio_window.sample_rate)
            if audio_window else None
        )
        answers = inputs.answers
        has_answer = any(answer.answer.strip() for answer in answers)
        if not (
            window is not None
            or audio is not None
            or has_answer
            or inputs.tongue_quality is not None
            or inputs.tongue_recognition is not None
            or inputs.constitution is not None
            or inputs.constitution_report is not None
        ):
            raise DetectionInputError("全部检测数据缺失，请先采集问诊或音视频数据")

        async def fetch_speech():
            return await run_blocking(self.speech.result, audio) if audio else None

        behavior, physiology, speech = await gather_and_cancel_siblings(
            asyncio.create_task(observe_behavior(self.behavior, window)),
            asyncio.create_task(measure_video_physiology(reader, window)),
            asyncio.create_task(fetch_speech()),
        )
        measured = video_physiology_fact(physiology)
        assessment, advice = assess_behavior(behavior, self.fatigue_scoring, self.fatigue_advice)
        if not has_answer and all(
            value is None for value in (
                behavior, measured, speech,
                inputs.tongue_quality, inputs.tongue_recognition,
                inputs.constitution, inputs.constitution_report,
            )
        ):
            raise DetectionInputError("缺少可用于生成健康报告的观察或回答")
        return HealthMeasurements(
            behavior=behavior, fatigue=assessment, advice=advice, speech=speech,
            physiology=measured, tongue_quality=inputs.tongue_quality,
            tongue_recognition=inputs.tongue_recognition, constitution=inputs.constitution,
            constitution_report=inputs.constitution_report, answers=answers,
            detection_id=inputs.detection_id, diagnostics=physiology_diagnostics(physiology, window),
        )

    async def write(self, measured: HealthMeasurements) -> HealthReportResult:
        facts = HealthReportFacts(
            tongue_quality=tongue_quality_fact(measured.tongue_quality),
            tongue_recognition=tongue_recognition_fact(measured.tongue_recognition),
            constitution=constitution_assessment_fact(measured.constitution),
            constitution_report=constitution_report_fact(measured.constitution_report),
            answers=measured.answers,
            behavior=behavior_observation_fact(measured.behavior),
            fatigue=fatigue_assessment_fact(measured.fatigue),
            advice=fatigue_advice_fact(measured.advice),
            speech=speech_fact(measured.speech),
            physiology=measured.physiology,
        )
        report = await run_blocking(self.health_report.result, facts)
        return HealthReportResult(
            behavior=measured.behavior, fatigue=measured.fatigue, advice=measured.advice,
            speech=measured.speech, physiology=measured.physiology,
            tongue_quality=measured.tongue_quality, tongue_recognition=measured.tongue_recognition,
            constitution=measured.constitution, constitution_report=measured.constitution_report,
            answers=measured.answers, detection_id=measured.detection_id,
            diagnostics=measured.diagnostics, report=report,
        )
