"""检测与报告只消费 FacePhys，同一视频 pass 产出全部生理指标。

被测主体是检测测量链（``server.detection.flows.create_measure``）：
传感器心率/体温窗口不进入报告事实，FacePhys 是唯一生理来源；一次视频
pass 同时产出心率与 HRV。
"""

import asyncio
from types import SimpleNamespace

import pytest

from server.capture.domain import HeartRateWindow, TemperatureWindow
from server.compute.facephys.types import VideoAnalysisInput
from server.compute.fatigue.service import FatigueAdviceService, FatigueScoringService
from server.concurrency import TaskAdmission
from server.detection.flows import create_measure
from server.detection_errors import DetectionInputError, DetectionUnavailableError
from server.tests.test_gateway_operations import (
    FakeBehavior,
    FakeCapture,
    FakeExpression,
    FakeMeasurementReader,
    FakeSpeech,
    PHYSIOLOGY_RESULT,
    physiology_ok,
    valid_behavior,
    FakeWindow,
)


class _UnreadReport:
    """measure 路径不得触碰解读文本端口。"""

    def result(self, facts):
        raise AssertionError("measure 不得触碰报告文本端口")


def make_measure(kind, reader, capture=None):
    """按报告类型装配真实测量链；reports 端口只提供类型事实。"""
    from server.analysis.emotion.assembly import create_emotion_analysis_flow_service
    from server.analysis.fatigue.reporting.flow import FatigueAnalysisFlowService
    from server.analysis.health.assembly import create_health_analysis_flow_service

    reports = SimpleNamespace(report_facts=lambda report_id: {"type": kind, "result": {}})
    if kind == "fatigue":
        fatigue_flow = FatigueAnalysisFlowService(
            behavior=FakeBehavior(valid_behavior()),
            fatigue_scoring=FatigueScoringService(),
            fatigue_advice=FatigueAdviceService(),
            facephys_factory=lambda: reader,
        )
        emotion_flow = health_flow = None
    elif kind == "emotion":
        fatigue_flow = None
        emotion_flow = create_emotion_analysis_flow_service(
            expression=FakeExpression(), behavior=FakeBehavior(valid_behavior()),
            facephys_factory=lambda: reader, speech=FakeSpeech(),
            fatigue_scoring=FatigueScoringService(), fatigue_advice=FatigueAdviceService(),
        )
        health_flow = None
    else:
        fatigue_flow = emotion_flow = None
        health_flow = create_health_analysis_flow_service(
            behavior=FakeBehavior(valid_behavior()),
            facephys_factory=lambda: reader, speech=FakeSpeech(),
            fatigue_scoring=FatigueScoringService(), fatigue_advice=FatigueAdviceService(),
        )
    return create_measure(
        reports=reports, admission=TaskAdmission(),
        fatigue_flow=fatigue_flow, emotion_flow=emotion_flow, health_flow=health_flow,
    )


def hardware_capture(**overrides):
    """只有传感器窗口的采集替身：心率/体温存在，但音视频缺失。"""
    return FakeCapture(
        heart_rate=HeartRateWindow(samples_bpm=(80.0,), average_bpm=80.0, latest_bpm=80.0),
        temperature=TemperatureWindow(object_temps=(36.5,)),
        **overrides,
    )


@pytest.mark.parametrize("kind", ["fatigue", "emotion", "health"])
def test_facephys_is_the_only_physiology_source(kind):
    video_reader = FakeMeasurementReader(physiology_ok())
    capture = FakeCapture(
        window=FakeWindow(),
        heart_rate=HeartRateWindow(samples_bpm=(80.0,), average_bpm=80.0, latest_bpm=80.0),
        temperature=TemperatureWindow(object_temps=(36.5,)),
    )

    result = asyncio.run(make_measure(kind, video_reader)(7, capture, ()))

    facts = result["facts"]
    assert facts["physiology"] == PHYSIOLOGY_RESULT
    assert {"heart_rate", "temperature"}.isdisjoint(facts)
    assert len(video_reader.calls) == 1
    assert isinstance(video_reader.calls[0], VideoAnalysisInput)


@pytest.mark.parametrize("kind", ["fatigue", "emotion", "health"])
def test_hardware_only_capture_does_not_supply_detection_evidence(kind):
    reader = FakeMeasurementReader(physiology_ok())
    capture = hardware_capture()

    with pytest.raises(DetectionInputError):
        asyncio.run(make_measure(kind, reader)(7, capture, ()))

    assert reader.calls == []


@pytest.mark.parametrize("kind", ["emotion", "health", "fatigue"])
def test_facephys_failure_propagates(kind):
    unavailable = DetectionUnavailableError("FacePhys unavailable")
    reader = FakeMeasurementReader(error=unavailable)
    capture = FakeCapture(window=FakeWindow())

    with pytest.raises(DetectionUnavailableError) as caught:
        asyncio.run(make_measure(kind, reader)(7, capture, ()))

    assert caught.value is unavailable
