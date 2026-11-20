"""Direct emotion use-case verification, without HTTP DTOs or FastAPI."""

import asyncio
import subprocess
import sys
import threading
from dataclasses import asdict, replace
from uuid import uuid4
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pytest

from server.analysis.emotion.reporting.flow import EmotionAnalysisFlowService
from server.analysis.emotion.reporting.types import (
    EmotionReportInput,
    EmotionReportResult,
)
from server.detection_errors import (
    DetectionInputError, DetectionTimeoutError, DetectionUnavailableError, DetectionUpstreamError,
)
from server.analysis.emotion.reporting.text import OpenAIEmotionReportTextReader
from server.analysis.emotion.reporting.types import (
    EmotionReportFacts,
    EmotionReportText,
)
from server.compute.expression.types import ExpressionObservation
from server.compute.expression.types import ExpressionObservations
from server.compute.fatigue.types import BehaviorObservation, FatigueAdvice, FatigueAssessment
from server.compute.facephys.types import (
    VideoHrvMeasurement,
    VideoPhysiologyMeasurement,
)
from server.compute.speech.types import AudioData, Transcript, TranscriptSegment

ASR_PCM = b"\x01\x00" * 160


def window_mock():
    from server.capture.types import Frame

    image = np.zeros((8, 8, 3), dtype=np.uint8)
    return (Frame(data=image, timestamp=14.0), Frame(data=image, timestamp=14.13))


def audio_window_mock(pcm=ASR_PCM):
    return SimpleNamespace(pcm=pcm, sample_rate=16000)


def make_flow(**overrides):
    readers = Mock(result=Mock(return_value=None))
    ports = dict(
        expression=Mock(result=Mock(return_value=None), result_frames=Mock(return_value=None)),
        behavior=Mock(result_frames=Mock(return_value=None)),
        facephys_factory=lambda: readers,
        speech=Mock(result=Mock(return_value=None)),
        fatigue_scoring=Mock(result=Mock(
            side_effect=lambda behavior: None if behavior is None else FatigueAssessment(score=0, level="正常"))),
        fatigue_advice=Mock(result=Mock(side_effect=lambda assessment: (
            FatigueAdvice(level="数据不足", text="行为数据不足，无法判断疲劳程度；其他可用指标仍已完成分析。")
            if assessment is None
            else FatigueAdvice(level="状态良好", text="测试建议文本")
        ))),
        emotion_report=Mock(result=Mock(return_value=EmotionReportText("情绪报告"))),
    )
    ports.update(overrides)
    media = Mock(video_window=Mock(return_value=None), audio_window=Mock(return_value=None))
    return EmotionAnalysisFlowService(**ports), media


async def run_report(flow, inputs, media):
    return await flow.write(await flow.measure(inputs, media))


def run(flow, media, inputs=None):
    inputs = replace(inputs or EmotionReportInput(), detection_id=uuid4().hex)
    return asyncio.run(run_report(flow, inputs, media))


def behavior_fact():
    return BehaviorObservation(
        blink_count=12, blink_frequency=12.0, yawn_count=1, nod_count=0,
        nod_frequency=0.0, perclos_percent=12.0, speaking_percent=8.0,
        face_coverage=0.97,
    )


@pytest.mark.parametrize("video_available", [False, True])
def test_data_driven_measurement_inputs_and_shared_video_pass(tmp_path, video_available):
    from server.compute.facephys.types import VideoAnalysisInput

    flow, media = make_flow()
    media.video_window.return_value = window_mock() if video_available else None
    media.audio_window.return_value = audio_window_mock()
    flow.speech.result.return_value = Transcript((TranscriptSegment("睡眠一般"),))
    flow.facephys_factory().result.return_value = VideoPhysiologyMeasurement(
        heart_rate_bpm=72.0,
        hrv=VideoHrvMeasurement(sdnn_ms=40.0, rmssd_ms=30.0, nn50=2,
                                pnn50_percent=10.0, rr_interval_count=3),
    )

    result = run(flow, media)

    assert isinstance(result, EmotionReportResult)
    if video_available:
        expected_input = flow.facephys_factory().result.call_args.args[0]
        assert isinstance(expected_input, VideoAnalysisInput)
        assert flow.facephys_factory().result.call_count == 1
        # 一次视频 pass 同时产出心率与 HRV
        assert result.physiology["heart_rate_bpm"] == 72.0 and result.physiology["hrv"] is not None
    else:
        # 无视频数据自然无视频测量：读取器根本不被调用。
        flow.facephys_factory().result.assert_not_called()
        assert result.physiology is None
    flow.speech.result.assert_called_once()
    assert flow.speech.result.call_args.args[0] == AudioData(pcm=ASR_PCM, sample_rate=16000)
    media.video_window.assert_called_once()



def test_facts_are_passed_to_text_with_scoring_and_advice(tmp_path):
    flow, media = make_flow()
    media.video_window.return_value = window_mock()
    flow.expression.result_frames.return_value = ExpressionObservations(
        (ExpressionObservation("happy", 0.9, 0.0, 2),), (("happy", 1),),
    )
    flow.behavior.result_frames.return_value = behavior_fact()
    result = run(flow, media)

    flow.fatigue_scoring.result.assert_called_once_with(result.behavior)
    flow.fatigue_advice.result.assert_called_once_with(result.fatigue)
    facts = flow.emotion_report.result.call_args.args[0]
    assert isinstance(facts, EmotionReportFacts)
    assert facts.expression == asdict(result.expression)
    assert facts.behavior == asdict(result.behavior)
    assert facts.fatigue == asdict(result.fatigue)
    assert facts.advice == {"level": result.advice.level, "text": result.advice.text}
    assert facts.speech is None and facts.physiology is None
    assert result.advice == FatigueAdvice(level="状态良好", text="测试建议文本")
    assert result.report is flow.emotion_report.result.return_value
    assert len(result.detection_id) == 32
    flow.speech.result.assert_not_called()


@pytest.mark.parametrize("fact", ["expression", "behavior", "physiology", "speech"])
def test_one_fact_suffices_and_other_observations_remain_none(tmp_path, fact):
    flow, media = make_flow()
    media.video_window.return_value = window_mock()
    media.audio_window.return_value = audio_window_mock()
    values = dict(
        expression=ExpressionObservations(
            (ExpressionObservation("happy", 0.9, 0.0, 2),), (("happy", 1),)),
        behavior=behavior_fact(),
        physiology=VideoPhysiologyMeasurement(respiration_rate_per_min=15.0),
        speech=Transcript((TranscriptSegment("事实"),)),
    )
    entry = "result_frames" if fact in ("expression", "behavior") else "result"
    if fact == "physiology":
        flow.facephys_factory().result.return_value = values[fact]
    else:
        getattr(getattr(flow, fact), entry).return_value = values[fact]
    result = run(flow, media)
    if fact == "physiology":
        assert result.physiology == {"heart_rate_bpm": None, "respiration_rate_per_min": 15.0, "hrv": None}
    else:
        assert getattr(result, fact) is values[fact]
    for other in values.keys() - {fact}:
        assert getattr(result, other) is None


@pytest.mark.parametrize("has_input,message", [
    (False, "全部检测数据缺失"), (True, "缺少可用于生成情绪报告的观察数据"),
])
def test_no_input_or_no_valid_facts_is_rejected_without_id(has_input, message, monkeypatch):

    flow, media = make_flow()
    media.video_window.return_value = window_mock() if has_input else None
    with pytest.raises(DetectionInputError, match=message):
        run(flow, media)
    flow.emotion_report.result.assert_not_called()
    assert flow.fatigue_scoring.result.call_count == int(has_input)


@pytest.mark.parametrize("stage", ["hrv", "emotion_report"])
@pytest.mark.parametrize("error_type", [DetectionUnavailableError, DetectionUpstreamError, DetectionTimeoutError, TypeError])
def test_technical_errors_propagate_unchanged(tmp_path, stage, error_type):
    flow, media = make_flow()
    media.video_window.return_value = window_mock()
    flow.behavior.result_frames.return_value = behavior_fact()
    failure = error_type("technical failure")
    if stage == "hrv":
        flow.facephys_factory().result.side_effect = failure
    else:
        flow.emotion_report.result.side_effect = failure
    with pytest.raises(error_type) as caught:
        run(flow, media)
    assert caught.value is failure


@pytest.mark.parametrize("content", [None, "", " \n\t "])
def test_real_text_boundary_rejects_blank_reports_before_id(content, monkeypatch):

    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **_: (
        SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])
    ))))
    flow, media = make_flow(emotion_report=OpenAIEmotionReportTextReader(lambda: client))
    media.video_window.return_value = window_mock()
    flow.behavior.result_frames.return_value = behavior_fact()
    with pytest.raises(DetectionUpstreamError, match="空内容"):
        run(flow, media)


def test_independent_branches_overlap_and_report_waits_for_facts(tmp_path):
    expression_started, behavior_started, release = threading.Event(), threading.Event(), threading.Event()
    flow, media = make_flow()
    media.video_window.return_value = window_mock()
    media.audio_window.return_value = audio_window_mock()

    def expression(_):
        expression_started.set()
        assert behavior_started.wait(3)
        assert release.wait(3)
        return None

    def behavior(_frames):
        behavior_started.set()
        assert expression_started.wait(3)
        assert release.wait(3)
        return behavior_fact()

    def speech(_audio):
        assert expression_started.wait(3)
        assert behavior_started.wait(3)
        flow.fatigue_scoring.result.assert_not_called()
        flow.emotion_report.result.assert_not_called()
        release.set()
        return None

    flow.expression.result_frames.side_effect = expression
    flow.behavior.result_frames.side_effect = behavior
    flow.speech.result.side_effect = speech
    try:
        run(flow, media)
    finally:
        release.set()
    flow.emotion_report.result.assert_called_once()


@pytest.mark.parametrize("stage", ["behavior", "emotion_report"])
def test_repeated_cancellation_waits_for_actual_work(stage):
    ready, release = threading.Event(), threading.Event()
    flow, media = make_flow()
    media.video_window.return_value = window_mock()
    flow.behavior.result_frames.return_value = behavior_fact()
    observed = []

    def worker(*_, **__):
        ready.set()
        assert release.wait(3)
        observed.append("task evidence")
        return {
            "behavior": behavior_fact(),
            "emotion_report": EmotionReportText("报告"),
        }[stage]

    if stage == "behavior":
        flow.behavior.result_frames.side_effect = worker
    else:
        flow.emotion_report.result.side_effect = worker

    async def scenario():
        task = asyncio.create_task(run_report(flow, EmotionReportInput(), media))
        try:
            assert await asyncio.to_thread(ready.wait, 3)
            for _ in range(2):
                task.cancel()
                await asyncio.sleep(0.01)
                assert not task.done()
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await task
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())
    assert observed == ["task evidence"]


def test_public_entry_import_and_execution_do_not_load_fastapi():
    # A fresh interpreter catches transitive HTTP imports hidden by pytest's fixtures.
    result = subprocess.run([sys.executable, "-c", '''
import asyncio
import sys
from unittest.mock import Mock
from server.analysis.emotion.reporting.flow import EmotionAnalysisFlowService
from server.analysis.emotion.reporting.types import EmotionReportInput
from server.detection_errors import DetectionInputError
media = Mock(video_window=Mock(return_value=None), audio_window=Mock(return_value=None))
flow = EmotionAnalysisFlowService(
    expression=Mock(), behavior=Mock(), facephys_factory=Mock(
        return_value=Mock(result=Mock(return_value=None))),
    speech=Mock(), emotion_report=Mock(),
    fatigue_scoring=Mock(), fatigue_advice=Mock())
try:
    asyncio.run(flow.measure(EmotionReportInput(), media))
except DetectionInputError:
    pass
else:
    raise AssertionError("expected missing input")
assert "fastapi" not in sys.modules
assert not any(name.startswith("server.presentation.gateway") for name in sys.modules)
'''], cwd=Path(__file__).resolve().parents[2], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_reused_flow_keeps_results_local_to_each_call(tmp_path):
    flow, media = make_flow()
    media.video_window.return_value = window_mock()
    flow.behavior.result_frames.return_value = behavior_fact()
    first = run(flow, media)
    flow.behavior.result_frames.return_value = None
    media.video_window.return_value = None
    media.audio_window.return_value = audio_window_mock()
    flow.speech.result.return_value = Transcript((TranscriptSegment("第二次"),))
    second = run(flow, media)
    assert first.behavior is not None and first.speech is None
    assert second.behavior is None and second.fatigue is None
    assert second.speech is flow.speech.result.return_value
    assert first.detection_id != second.detection_id
