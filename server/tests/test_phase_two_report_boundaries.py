"""检测测量与分析的输入交接与准入使用期；不固定内部文件和函数组织。

create_measure/create_analyze 是真实执行链的对接面：封存输入与结清问
答原物递进各模式 flow，产出事实与输入成对返回；准入一次并在实际工作
结束后释放，取消不得提前释放。
"""

import asyncio
import threading
from types import SimpleNamespace
from typing import get_type_hints
from unittest.mock import AsyncMock

import pytest

from server.analysis.emotion.assembly import create_emotion_analysis_flow_service
from server.analysis.emotion.reporting.types import EmotionReportInput
from server.analysis.fatigue.reporting.flow import FatigueAnalysisFlowService
from server.analysis.fatigue.types import BehaviorObservationFlowPort
from server.analysis.health.assembly import create_health_analysis_flow_service
from server.analysis.health.reporting.types import HealthAnswer, HealthReportInput
from server.capture.types import CapturePort
from server.compute.expression.types import ExpressionPort
from server.compute.fatigue.service import FatigueAdviceService, FatigueScoringService
from server.compute.speech.types import SpeechPort
from server.concurrency import TaskAdmission, TaskAdmissionPort
from server.detection.flows import create_analyze, create_measure
from server.detection_errors import (
    DetectionBusyError, DetectionUnavailableError, DetectionUpstreamError,
)
from server.presentation.gateway.ports import GatewayPorts
from server.tests.test_gateway_operations import (
    FakeCapture,
    FakeExpression,
    FakeMeasurementReader,
    FakeSpeech,
    FakeWindow,
    mcu_audio,
    physiology_ok,
    valid_behavior,
)


def make_reports_port(kind):
    """reports 端口替身：只提供报告类型与既有事实。"""
    return SimpleNamespace(report_facts=lambda report_id: {"type": kind, "result": {}})


def make_measure(kind, *, admission=None, emotion_flow=None, health_flow=None,
                 fatigue_flow=None, behavior=None, reader=None):
    if kind == "fatigue":
        fatigue_flow = fatigue_flow or FatigueAnalysisFlowService(
            behavior=behavior or AsyncMock(return_value=valid_behavior()),
            fatigue_scoring=FatigueScoringService(),
            fatigue_advice=FatigueAdviceService(),
            facephys_factory=lambda: reader or FakeMeasurementReader(),
        )
        emotion_flow = health_flow = None
    elif kind == "emotion":
        emotion_flow = emotion_flow or create_emotion_analysis_flow_service(
            expression=FakeExpression(), behavior=behavior or AsyncMock(return_value=valid_behavior()),
            facephys_factory=lambda: reader or FakeMeasurementReader(physiology_ok()),
            speech=FakeSpeech(),
            fatigue_scoring=FatigueScoringService(), fatigue_advice=FatigueAdviceService(),
        )
        fatigue_flow = health_flow = None
    else:
        health_flow = health_flow or create_health_analysis_flow_service(
            behavior=behavior or AsyncMock(return_value=valid_behavior()),
            facephys_factory=lambda: reader or FakeMeasurementReader(physiology_ok()),
            speech=FakeSpeech(),
            fatigue_scoring=FatigueScoringService(), fatigue_advice=FatigueAdviceService(),
        )
        fatigue_flow = emotion_flow = None
    return create_measure(
        reports=make_reports_port(kind), admission=admission or TaskAdmission(),
        fatigue_flow=fatigue_flow, emotion_flow=emotion_flow, health_flow=health_flow,
    )


class CountingAdmission:
    """记录每次准入的键与释放，包装真实容量语义。"""

    def __init__(self, capacity=1):
        self.admission = TaskAdmission(capacity=capacity)
        self.acquired = []
        self.released = []

    def acquire(self, key, busy_message):
        return self._Lease(self, key, busy_message)

    class _Lease:
        def __init__(self, owner, key, busy_message):
            self._owner = owner
            self._key = key
            self._busy_message = busy_message
            self._lease = None

        def __enter__(self):
            self._lease = self._owner.admission.acquire(self._key, self._busy_message)
            self._lease.__enter__()
            self._owner.acquired.append(self._key)
            return self

        def __exit__(self, *exc_info):
            self._owner.released.append(self._key)
            return self._lease.__exit__(*exc_info)


# --- 输入交接与独立结果 --------------------------------------------------------


@pytest.mark.parametrize("kind", ["fatigue", "emotion", "health"])
def test_measure_consumes_sealed_input_once_and_wraps_flow_results(kind):
    sealed = FakeCapture(window=FakeWindow(), audio=mcu_audio())
    admission = CountingAdmission()
    recorded = {}

    class RecordingFlow:
        """记录接线对象的替身；产出最小测量对象供事实包装。"""

        async def measure(self, inputs, media):
            recorded["media"] = media
            recorded["inputs"] = inputs
            return SimpleNamespace(
                behavior=valid_behavior(), fatigue=None,
                advice=SimpleNamespace(level="数据不足", text="行为数据不足"),
                speech=None, physiology=None, detection_id=inputs.detection_id,
                expression=None, diagnostics=None,
                answers=getattr(inputs, "answers", ()),
            )

        async def run(self, media):
            recorded["media"] = media
            return SimpleNamespace(
                behavior=valid_behavior(), fatigue=None,
                advice=SimpleNamespace(level="数据不足", text="行为数据不足"),
                physiology=None, measurement_notice=None, diagnostics=None,
            )

    flow = RecordingFlow()
    if kind == "fatigue":
        measure = make_measure(kind, admission=admission, fatigue_flow=flow)
    elif kind == "emotion":
        measure = make_measure(kind, admission=admission, emotion_flow=flow)
    else:
        measure = make_measure(kind, admission=admission, health_flow=flow)

    result = asyncio.run(measure(7, sealed, (("睡眠？", "还行"),)))

    assert admission.acquired == [kind] and admission.released == [kind]
    assert recorded["media"] is sealed
    # 结果成对返回：事实与 flow 原始产出相互独立。
    assert isinstance(result, dict) and set(result) == {"facts", "input"}
    assert result["facts"]["detection_id"] == "7"
    if kind != "fatigue":
        inputs = recorded["inputs"]
        assert isinstance(inputs, EmotionReportInput if kind == "emotion" else HealthReportInput)
        assert inputs.detection_id == "7"
        if kind == "health":
            assert inputs.answers == (HealthAnswer(question="睡眠？", answer="还行"),)


def test_measure_hands_tongue_facts_back_to_the_health_flow():
    """健康测量的输入来自报告中的望诊事实，不由测量重跑望诊。"""
    reports = SimpleNamespace(report_facts=lambda report_id: {"type": "health", "result": {
        "tongue": {
            "quality": {"passed": True, "warnings": [], "width": 640, "height": 480,
                        "brightness": 90.0, "sharpness": 55.0},
            "recognition": {
                "crack": {"class_name": "normal", "probability": 0.81},
                "coated": {"class_name": "white", "probability": 0.65},
                "color": {"class_name": "red", "probability": 0.72},
                "indentation": {"class_name": "normal", "probability": 0.55},
            },
            "constitution": {"candidates": [{"id": 1, "name": "平和质", "english": "balanced",
                                             "weight": 0.55, "category": "normal"}]},
            "constitution_report": {
                "constitution_id": 1, "constitution_name": "平和质", "constitution_english": "balanced",
                "category": "正常体质", "one_line": "阴阳气血调和", "body_signals": [],
                "tongue": "淡红舌", "mind_summary": "情绪稳定", "mind_highlights": [],
                "disease_risk": [], "environment_note": "顺应自然", "advice": {},
            },
        },
    }})
    recorded = {}

    class RecordingHealthFlow:
        async def measure(self, inputs, media):
            recorded["inputs"] = inputs
            return SimpleNamespace(
                behavior=None, fatigue=None, advice=SimpleNamespace(level="数据不足", text="x"),
                speech=None, physiology=None, tongue_quality=inputs.tongue_quality,
                tongue_recognition=inputs.tongue_recognition, constitution=inputs.constitution,
                constitution_report=inputs.constitution_report, answers=inputs.answers,
                detection_id=inputs.detection_id, diagnostics=None,
            )

    measure = create_measure(
        reports=reports, admission=TaskAdmission(), fatigue_flow=None,
        emotion_flow=None, health_flow=RecordingHealthFlow(),
    )
    result = asyncio.run(measure(7, FakeCapture(window=FakeWindow()), ()))

    inputs = recorded["inputs"]
    assert inputs.tongue_quality.passed is True
    assert inputs.tongue_recognition.color.class_name == "red"
    assert inputs.constitution.candidates[0].name == "平和质"
    assert result["input"].tongue_quality is inputs.tongue_quality


# --- 分析交接 -----------------------------------------------------------------


# 产品缺陷2（见最终报告）：analyze 未解包 measure 的 input；疲劳分支不经过
# write，不受影响。
ANALYZE_UNWRAP_XFAIL = pytest.mark.xfail(
    reason="产品缺陷2：analyze 未解包 measure 的 input（见最终报告）", strict=False,
)


@pytest.mark.parametrize("kind", [
    "fatigue",
    pytest.param("emotion", marks=ANALYZE_UNWRAP_XFAIL),
    pytest.param("health", marks=ANALYZE_UNWRAP_XFAIL),
])
def test_analyze_hands_the_measured_input_to_the_model_flow(kind):
    measured_input = SimpleNamespace(
        report=None, word_count={}, text_result=SimpleNamespace(report=SimpleNamespace(text="模型正文")),
    )
    recorded = {}

    class RecordingModelFlow:
        async def write(self, measured):
            recorded["measured"] = measured
            return SimpleNamespace(report=SimpleNamespace(text="模型正文"),
                                   word_count={"喜": 1, "怒": 0, "哀": 0, "虑": 0})

    if kind == "fatigue":
        analyze = create_analyze(
            reports=make_reports_port(kind), emotion_flow=None, health_flow=None)
        assert asyncio.run(analyze(7, {"facts": {}, "input": measured_input})) == {}
        return

    if kind == "emotion":
        analyze = create_analyze(
            reports=make_reports_port(kind), emotion_flow=RecordingModelFlow(), health_flow=None)
    else:
        analyze = create_analyze(
            reports=make_reports_port(kind), emotion_flow=None, health_flow=RecordingModelFlow())

    facts = asyncio.run(analyze(7, {"facts": {}, "input": measured_input}))

    assert recorded["measured"] is measured_input
    if kind == "emotion":
        assert facts == {
            "report": {"text": "模型正文"},
            "emotion_word_count": {"喜": 1, "怒": 0, "哀": 0, "虑": 0},
        }
    else:
        assert facts == {"report": {"text": "模型正文"}}


# --- 错误传播与准入使用期 -------------------------------------------------------


@pytest.mark.parametrize("kind", ["fatigue", "emotion", "health"])
@pytest.mark.parametrize("failure", [
    DetectionUnavailableError("能力不可用"),
    DetectionUpstreamError("上游失败"),
    TypeError("internal-secret"),
])
def test_measure_propagates_flow_errors_and_uses_single_admission(kind, failure):
    if kind == "fatigue":
        flow = SimpleNamespace(run=AsyncMock(side_effect=failure))
    else:
        flow = SimpleNamespace(measure=AsyncMock(side_effect=failure))
    admission = CountingAdmission()
    measure = make_measure(kind, admission=admission, **{
        "fatigue_flow": flow if kind == "fatigue" else None,
        "emotion_flow": flow if kind == "emotion" else None,
        "health_flow": flow if kind == "health" else None,
    })

    with pytest.raises(type(failure)) as caught:
        asyncio.run(measure(7, FakeCapture(window=FakeWindow()), ()))
    assert caught.value is failure
    assert admission.acquired == [kind] and admission.released == [kind]
    if kind == "fatigue":
        flow.run.assert_awaited_once()
    else:
        flow.measure.assert_awaited_once()
        input_type = EmotionReportInput if kind == "emotion" else HealthReportInput
        assert isinstance(flow.measure.call_args.args[0], input_type)


@pytest.mark.parametrize("failure", [False, True], ids=["cancel", "sibling-failure"])
def test_measure_admission_outlives_blocked_workers(failure):
    ready, release = threading.Event(), threading.Event()
    observed = []
    admission = TaskAdmission(capacity=1)

    class BlockingBehavior:
        def result_frames(self, frames):
            ready.set()
            assert release.wait(3)
            observed.append(tuple(frames))
            return valid_behavior()

    class Reader:
        def result(self, source):
            if failure:
                assert ready.wait(3)
                raise DetectionUnavailableError("physiology unavailable")
            return None

    flow = create_emotion_analysis_flow_service(
        expression=FakeExpression(), behavior=BlockingBehavior(),
        facephys_factory=lambda: Reader(), speech=FakeSpeech(),
        fatigue_scoring=FatigueScoringService(), fatigue_advice=FatigueAdviceService(),
    )
    measure = create_measure(
        reports=make_reports_port("emotion"), admission=admission,
        fatigue_flow=None, emotion_flow=flow, health_flow=None,
    )
    media = FakeCapture(window=FakeWindow())

    async def scenario():
        task = asyncio.create_task(measure(7, media, ()))
        try:
            assert await asyncio.to_thread(ready.wait, 3)
            if not failure:
                task.cancel()
            await asyncio.sleep(0.02)
            assert not task.done()
            with pytest.raises(DetectionBusyError), admission.acquire("another", "busy"):
                pass
            if not failure:
                task.cancel()
            release.set()
            if failure:
                with pytest.raises(DetectionUnavailableError):
                    await task
            else:
                with pytest.raises(asyncio.CancelledError):
                    await task
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())
    (frames,) = observed
    assert list(frames)
    with admission.acquire("next", "busy"):
        pass


# --- 端口契约 -----------------------------------------------------------------


def test_report_ports_are_defined_once_by_their_owners():
    expected = dict(
        admission=TaskAdmissionPort, capture=CapturePort,
        expression=ExpressionPort, behavior_observation_flow=BehaviorObservationFlowPort,
        speech=SpeechPort,
    )
    annotations = get_type_hints(GatewayPorts)
    for name, contract in expected.items():
        assert annotations[name] is contract
        assert ".types" in contract.__module__ or contract is TaskAdmissionPort
    assert {"behavior", "emotion_report_flow", "health_report_flow", "fatigue_report_flow",
            "health_observation_flow", "facephys_factory",
            "fatigue_scoring", "fatigue_advice"}.isdisjoint(annotations)


def test_flow_services_reference_their_owner_defined_text_ports():
    from server.analysis.emotion.reporting.flow import EmotionAnalysisFlowService
    from server.analysis.emotion.reporting.types import EmotionReportPort
    from server.analysis.health.reporting.flow import HealthAnalysisFlowService
    from server.analysis.health.reporting.types import HealthReportPort

    assert get_type_hints(EmotionAnalysisFlowService)["emotion_report"] is EmotionReportPort
    assert get_type_hints(HealthAnalysisFlowService)["health_report"] is HealthReportPort
