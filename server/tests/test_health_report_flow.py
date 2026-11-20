"""Direct health use-case verification, without HTTP DTOs or FastAPI."""

import asyncio
import json
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

from server.analysis.health.reporting.flow import HealthAnalysisFlowService
from server.analysis.health.reporting.types import (
    HealthReportInput,
    HealthReportResult,
)
from server.compute.tongue.types import ConstitutionAssessment, ConstitutionCandidate
from server.compute.tongue.types import ConstitutionReport
from server.detection_errors import (
    DetectionInputError, DetectionTimeoutError, DetectionUnavailableError, DetectionUpstreamError,
)
from server.compute.fatigue.types import BehaviorObservation, FatigueAdvice, FatigueAssessment
from server.analysis.health.reporting.text import HealthReportTextPort
from server.analysis.health.reporting.text import OpenAIHealthReportTextReader
from server.analysis.health.facts import HealthAnswer
from server.analysis.health.reporting.types import (
    HealthReportFacts,
    HealthReportText,
)
from server.compute.facephys.types import (
    VideoHrvMeasurement,
    VideoPhysiologyMeasurement,
)
from server.compute.speech.types import AudioData, Transcript, TranscriptSegment
from server.compute.tongue.types import TongueQuality
from server.compute.tongue.types import TongueDimensionResult, TongueRecognition

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
        health_report=Mock(result=Mock(return_value=HealthReportText("健康报告"))),
    )
    ports.update(overrides)
    media = Mock(video_window=Mock(return_value=None), audio_window=Mock(return_value=None))
    return HealthAnalysisFlowService(**ports), media


async def run_report(flow, inputs, media):
    return await flow.write(await flow.measure(inputs, media))


def run(flow, media, inputs=None):
    inputs = replace(inputs or HealthReportInput(), detection_id=uuid4().hex)
    return asyncio.run(run_report(flow, inputs, media))


def behavior_fact():
    return BehaviorObservation(
        blink_count=12, blink_frequency=12.0, yawn_count=1, nod_count=0,
        nod_frequency=0.0, perclos_percent=12.0, speaking_percent=8.0,
        face_coverage=0.97,
    )


def submitted_facts():
    return dict(
        tongue_quality=TongueQuality(False, ("画面偏暗",), 320, 240, 40.0, 55.0),
        tongue_recognition=TongueRecognition(
            TongueDimensionResult("normal", 0.9), TongueDimensionResult("white", 0.8),
            TongueDimensionResult("red", 0.7), TongueDimensionResult("normal", 0.6),
        ),
        constitution=ConstitutionAssessment((
            ConstitutionCandidate(1, "平和质", "Balanced", 0.9, "normal"),
        )),
        constitution_report=ConstitutionReport(
            2, "气虚质", "Qi deficient", "偏颇体质", "用户提交的规则报告",
            (), "淡红舌", "原记录", (), (), "原建议", {"diet": ["饮食建议"]},
        ),
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

    assert isinstance(result, HealthReportResult)
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




def test_settled_answers_pass_through_without_question_audio_slicing(tmp_path):
    flow, media = make_flow()
    media.audio_window.return_value = audio_window_mock()
    flow.speech.result.return_value = Transcript((TranscriptSegment("整段转写"),))
    inputs = HealthReportInput(answers=(
        HealthAnswer(question="问题一", answer="答案一"),
        HealthAnswer(question="问题二"),
    ))
    result = run(flow, media, inputs=inputs)

    # 逐题答案随会话结清注入：报告流程不再按题切片转写，整段转写只跑一次。
    assert result.answers == inputs.answers
    flow.speech.result.assert_called_once()
    facts = flow.health_report.result.call_args.args[0]
    assert isinstance(facts, HealthReportFacts)
    assert facts.answers == result.answers
    assert facts.speech == {"segments": [{"text": "整段转写"}]}


@pytest.mark.parametrize("fact", ["tongue_quality", "tongue_recognition", "constitution", "constitution_report"])
def test_each_submitted_tongue_fact_suffices_without_camera_or_reanalysis(tmp_path, fact):
    flow, media = make_flow()
    value = submitted_facts()[fact]
    inputs = HealthReportInput(**{fact: value})
    result = run(flow, media, inputs=inputs)

    assert getattr(result, fact) is value
    assert getattr(flow.health_report.result.call_args.args[0], fact) == asdict(value)
    for other in submitted_facts().keys() - {fact}:
        assert getattr(result, other) is None
    assert result.behavior is result.fatigue is result.speech is None
    # 无视频窗口时不运行任何视频分析。
    flow.behavior.result_frames.assert_not_called()


@pytest.mark.parametrize("speech_text", ["睡眠很好", "睡眠很差"])
def test_real_text_payload_preserves_submitted_facts_and_speech(
    tmp_path, speech_text, monkeypatch,
):
    from server import settings

    monkeypatch.setitem(settings.AI_CONFIG, "api_key", "test-key")
    calls = []
    completion = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="报告"))])
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(
        create=lambda **kwargs: (calls.append(kwargs), completion)[1],
    )))
    flow, media = make_flow(health_report=HealthReportTextPort(OpenAIHealthReportTextReader(lambda: client)))
    media.audio_window.return_value = audio_window_mock()
    speech = Transcript((TranscriptSegment(speech_text), TranscriptSegment("补充描述")))
    flow.speech.result.return_value = speech
    facts = submitted_facts()
    inputs = HealthReportInput(
        **facts, answers=(HealthAnswer(question="问题列表的独立提问", answer="口头回答"),),
    )

    result = run(flow, media, inputs=inputs)

    assert len(calls) == 1
    payload = json.loads(calls[0]["messages"][1]["content"])
    for name, value in facts.items():
        assert getattr(result, name) is value
        assert payload[name] == json.loads(json.dumps(asdict(value)))
    assert payload["tongue_quality"]["passed"] is False
    assert payload["constitution"]["candidates"][0]["id"] == 1
    assert payload["constitution_report"]["constitution_id"] == 2
    # 逐题答案随会话结清注入，整段转写作为独立事实单独递给模型。
    assert payload["answers"] == [{"question": "问题列表的独立提问", "answer": "口头回答"}]
    assert payload["speech"] == {"segments": [{"text": speech_text}, {"text": "补充描述"}]}
    assert result.speech is speech
    assert result.answers == inputs.answers
    assert "不得自行配给问题" in calls[0]["messages"][0]["content"]


@pytest.mark.parametrize("fact", ["behavior", "physiology", "speech"])
def test_one_observed_fact_suffices_with_empty_answers_and_other_facts_missing(tmp_path, fact):
    flow, media = make_flow()
    media.video_window.return_value = window_mock()
    media.audio_window.return_value = audio_window_mock()
    values = dict(
        behavior=behavior_fact(),
        physiology=VideoPhysiologyMeasurement(respiration_rate_per_min=15.0),
        speech=Transcript((TranscriptSegment("整段事实"),)),
    )
    entry = "result_frames" if fact == "behavior" else "result"
    if fact == "physiology":
        flow.facephys_factory().result.return_value = values[fact]
    else:
        getattr(getattr(flow, fact), entry).return_value = values[fact]
    inputs = HealthReportInput(answers=(HealthAnswer(question="没有逐题作答的问题"),))
    result = run(flow, media, inputs=inputs)
    if fact == "physiology":
        assert result.physiology == {"heart_rate_bpm": None, "respiration_rate_per_min": 15.0, "hrv": None}
    else:
        assert getattr(result, fact) is values[fact]
    for other in values.keys() - {fact}:
        assert getattr(result, other) is None
    assert result.answers == inputs.answers
    flow.fatigue_scoring.result.assert_called_once_with(result.behavior)
    flow.fatigue_advice.result.assert_called_once_with(result.fatigue)
    facts = flow.health_report.result.call_args.args[0]

    def evidence(value):
        return asdict(value) if value is not None else None

    assert facts.behavior == evidence(result.behavior)
    assert facts.physiology == result.physiology
    assert facts.speech == ({"segments": [{"text": "整段事实"}]} if result.speech is not None else None)
    assert facts.fatigue == evidence(result.fatigue)
    assert facts.advice == {"level": result.advice.level, "text": result.advice.text}
    assert facts.answers == result.answers
    assert result.report is flow.health_report.result.return_value
    assert len(result.detection_id) == 32


@pytest.mark.parametrize("has_input", [False, True])
def test_empty_answers_without_observations_are_rejected_without_text_or_id(tmp_path, has_input, monkeypatch):

    flow, media = make_flow()
    media.video_window.return_value = (
        window_mock() if has_input else None
    )
    inputs = HealthReportInput(answers=(HealthAnswer(question="仅有空回答不足以生成报告"),))
    message = "缺少可用于生成健康报告" if has_input else "全部检测数据缺失"
    with pytest.raises(DetectionInputError, match=message):
        run(flow, media, inputs=inputs)
    flow.health_report.result.assert_not_called()


@pytest.mark.parametrize("stage", ["hrv", "health_report"])
@pytest.mark.parametrize("error_type", [DetectionUnavailableError, DetectionUpstreamError, DetectionTimeoutError, TypeError])
def test_technical_errors_propagate_once_input_exists(tmp_path, stage, error_type):
    flow, media = make_flow()
    failure = error_type("technical failure")
    if stage == "hrv":
        media.video_window.return_value = window_mock()
        flow.facephys_factory().result.side_effect = failure
    else:
        media.video_window.return_value = window_mock()
        flow.facephys_factory().result.return_value = VideoPhysiologyMeasurement(heart_rate_bpm=80.0)
        flow.health_report.result.side_effect = failure
    with pytest.raises(error_type) as caught:
        run(flow, media)
    assert caught.value is failure
    if stage != "health_report":
        flow.health_report.result.assert_not_called()


@pytest.mark.parametrize("content", [None, "", " \n\t "])
def test_real_text_boundary_rejects_blank_reports_before_id(tmp_path, content, monkeypatch):
    from server import settings

    monkeypatch.setitem(settings.AI_CONFIG, "api_key", "test-key")
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **_: (
        SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])
    ))))
    flow, media = make_flow(health_report=HealthReportTextPort(OpenAIHealthReportTextReader(lambda: client)))
    media.video_window.return_value = window_mock()
    flow.facephys_factory().result.return_value = VideoPhysiologyMeasurement(heart_rate_bpm=80.0)
    with pytest.raises(DetectionUpstreamError, match="空内容"):
        run(flow, media)


def test_independent_branches_overlap_and_report_waits_for_facts(tmp_path):
    behavior_started, hrv_started, release = threading.Event(), threading.Event(), threading.Event()
    flow, media = make_flow()
    media.video_window.return_value = window_mock()
    media.audio_window.return_value = audio_window_mock()

    def behavior(_frames):
        behavior_started.set()
        assert hrv_started.wait(3)
        assert release.wait(3)
        return behavior_fact()

    def hrv(_):
        hrv_started.set()
        assert behavior_started.wait(3)
        assert release.wait(3)
        return None

    def speech(_audio):
        assert behavior_started.wait(3)
        assert hrv_started.wait(3)
        flow.fatigue_scoring.result.assert_not_called()
        flow.health_report.result.assert_not_called()
        release.set()
        return None

    flow.behavior.result_frames.side_effect = behavior
    flow.facephys_factory().result.side_effect = hrv
    flow.speech.result.side_effect = speech
    try:
        run(flow, media)
    finally:
        release.set()
    flow.health_report.result.assert_called_once()


@pytest.mark.parametrize("stage", ["behavior", "health_report"])
def test_repeated_cancellation_waits_for_actual_work(stage):
    ready, release = threading.Event(), threading.Event()
    flow, media = make_flow()
    media.video_window.return_value = window_mock()
    # 视频心率保证 health_report 阶段有事实可用。
    flow.facephys_factory().result.return_value = VideoPhysiologyMeasurement(heart_rate_bpm=80.0)
    observed = []

    def worker(*_, **__):
        ready.set()
        assert release.wait(3)
        observed.append("task evidence")
        return {
            "behavior": behavior_fact(),
            "health_report": HealthReportText("报告"),
        }[stage]

    if stage == "behavior":
        flow.behavior.result_frames.side_effect = worker
    else:
        flow.health_report.result.side_effect = worker

    async def scenario():
        task = asyncio.create_task(run_report(flow, HealthReportInput(), media))
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


def test_public_entry_import_and_execution_do_not_load_http_or_support():
    result = subprocess.run([sys.executable, "-c", '''
import asyncio
import sys
from unittest.mock import Mock
from server.analysis.health.reporting.flow import HealthAnalysisFlowService
from server.analysis.health.reporting.types import HealthReportInput
from server.detection_errors import DetectionInputError
media = Mock(video_window=Mock(return_value=None), audio_window=Mock(return_value=None))
flow = HealthAnalysisFlowService(
    behavior=Mock(), facephys_factory=Mock(
        return_value=Mock(result=Mock(return_value=None))),
    speech=Mock(), health_report=Mock(),
    fatigue_scoring=Mock(), fatigue_advice=Mock())
try:
    asyncio.run(flow.measure(HealthReportInput(), media))
except DetectionInputError:
    pass
else:
    raise AssertionError("expected missing input")
assert "fastapi" not in sys.modules
assert not any(name.startswith(("server.presentation.gateway", "server.support.identity", "server.support.reporting", "server.database",
                                "server.analysis.emotion")) for name in sys.modules)
'''], cwd=Path(__file__).resolve().parents[2], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_reused_flow_keeps_submitted_inputs_and_results_local_to_each_call(tmp_path):
    flow, media = make_flow()
    first_input = HealthReportInput(**submitted_facts())
    first = run(flow, media, first_input)
    media.audio_window.return_value = audio_window_mock()
    flow.speech.result.return_value = Transcript((TranscriptSegment("第二次整段转写"),))
    second = run(flow, media, HealthReportInput())
    for name, value in submitted_facts().items():
        assert getattr(first, name) == value
        assert getattr(second, name) is None
    assert first.answers == () and first.speech is None
    assert second.answers == () and second.speech is flow.speech.result.return_value
    assert first.detection_id != second.detection_id
