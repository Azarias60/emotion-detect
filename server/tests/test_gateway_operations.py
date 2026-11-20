"""Gateway observation operation behaviour across capture windows and observations.

观测处理器用真实能力数据类与替身端口驱动；路由测试用替身端口构建小
FastAPI 应用，让 HTTP 解析与公开信封共用同一批替身。检测意图与报告事
实的契约由 ``test_*_contract.py`` 与 ``test_error_policy.py`` 覆盖。
"""

import dataclasses
import inspect
import json

import numpy as np
import pytest
from fastapi.testclient import TestClient

from server.analysis.emotion.questions import DEFAULT_PSYCHOLOGY_QUESTIONS
from server.analysis.emotion.types import EmotionQuestionSet
from server.analysis.fatigue.observation import BehaviorObservationService
from server.analysis.health.observation import HealthObservationService
from server.analysis.health.questions import prepare_health_questions
from server.analysis.health.types import HealthQuestionSet
from server.compute.expression.assembly import create_expression_service
from server.compute.expression.expression_recognition.domain import summarize_expressions
from server.compute.expression.types import ExpressionObservation
from server.compute.fatigue.assembly import create_fatigue_observation_service
from server.compute.fatigue.behavior_recognition.domain import summarize_behavior
from server.compute.fatigue.fatigue_advice.domain import generate_fatigue_advice
from server.compute.fatigue.fatigue_scoring.application import assess_behavior
from server.compute.speech.assembly import create_speech_service
from server.compute.speech.types import AudioData, Transcript, TranscriptSegment
from server.compute.tongue.assembly import create_tongue_recognition_service
from server.compute.tongue.tongue_recognition.errors import InvalidImageError
from server.compute.tongue.types import (
    ConstitutionAssessment, ConstitutionCandidate, ConstitutionReport,
    TongueDimensionResult, TongueQuality, TongueRecognition, TongueResult,
)
from server.capture.domain import HeartRateWindow
from server.capture.types import Frame, TemperatureWindow
from server.concurrency import TaskAdmission
from server.detection_errors import DetectionInputError
from server.presentation.gateway.operations.observations import (
    handle_behavior_observation,
    handle_expression_observation,
    handle_speech_observation,
)
from server.presentation.gateway.ports import GatewayPorts
from server.tests.gateway_app_factory import make_gateway_app


def speech_ok():
    return Transcript((
        TranscriptSegment("你好。 最近有点累！"),
        TranscriptSegment("心率正常吗？"),
    ))


# --- real capability values -------------------------------------------------


def valid_behavior():
    return summarize_behavior(
        blink_count=12, yawn_count=1, nod_count=0,
        closed_seconds=0.12 * 60, speaking_seconds=0.08 * 60,
        face_seconds=60, video_seconds=60 / 0.97,
    )


def expression_ok():
    return summarize_expressions((
        ExpressionObservation(label="happy", confidence=0.9, timestamp=0.0, class_index=2),
        ExpressionObservation(label="neutral", confidence=0.8, timestamp=1.0, class_index=4),
    ))


def tongue_quality_ok(passed=True):
    warnings = () if passed else ("画面偏暗，请增加正面光线",)
    return TongueQuality(
        passed=passed, warnings=warnings, width=640, height=480,
        brightness=90.0, sharpness=55.0,
    )


def tongue_recognition_ok():
    return TongueRecognition(
        crack=TongueDimensionResult(class_name="normal", probability=0.81),
        coated=TongueDimensionResult(class_name="white", probability=0.65),
        color=TongueDimensionResult(class_name="red", probability=0.72),
        indentation=TongueDimensionResult(class_name="normal", probability=0.55),
    )


def constitution_ok():
    return ConstitutionAssessment(candidates=(
        ConstitutionCandidate(id=1, name="平和质", english="balanced", weight=0.55, category="normal"),
    ))


def constitution_report_ok():
    return ConstitutionReport(
        constitution_id=1, constitution_name="平和质", constitution_english="balanced",
        category="正常体质", one_line="阴阳气血调和", body_signals=(), tongue="淡红舌",
        mind_summary="情绪稳定", mind_highlights=(), disease_risk=(), environment_note="顺应自然",
        advice={},
    )


def tongue_result_ok(passed=True):
    return TongueResult(
        quality=tongue_quality_ok(passed),
        recognition=tongue_recognition_ok() if passed else None,
        constitution=constitution_ok() if passed else None,
        constitution_report=constitution_report_ok() if passed else None,
    )


def physiology_ok():
    from server.compute.facephys.types import (
        VideoHrvMeasurement, VideoPhysiologyMeasurement,
    )

    return VideoPhysiologyMeasurement(
        heart_rate_bpm=72.6,
        hrv=VideoHrvMeasurement(
            sdnn_ms=41.2, rmssd_ms=33.8, nn50=6, pnn50_percent=18.4,
            lf_hf_ratio=1.7, rr_interval_count=3,
        ),
        sqi=0.86, face_coverage=0.95,
    )


PHYSIOLOGY_RESULT = {
    "heart_rate_bpm": 72.6,
    "respiration_rate_per_min": None,
    "hrv": {
        "sdnn_ms": 41.2, "rmssd_ms": 33.8, "nn50": 6, "pnn50_percent": 18.4,
        "lf_hf_ratio": 1.7, "rr_interval_count": 3, "rr_source": "rppg_peak_derived",
    },
}
ADVICE_OK = {"level": "状态良好", "text": "当前未发现明显疲劳特征，请继续保持规律作息和适度休息。"}
ADVICE_MISSING = {
    "level": "数据不足",
    "text": "行为数据不足，无法判断疲劳程度；其他可用指标仍已完成分析。",
}


# --- fake ports -------------------------------------------------------------


class FakeAdmission:
    def __init__(self, busy=False):
        self.busy = busy
        self.calls = []

    def acquire(self, key, busy_message):
        return _FakeAdmissionLease(self, key, busy_message)


class _FakeAdmissionLease:
    def __init__(self, admission, key, busy_message):
        self._admission = admission
        self._key = key
        self._busy_message = busy_message

    def __enter__(self):
        self._admission.calls.append({"key": self._key, "busy_message": self._busy_message})
        if self._admission.busy:
            from server.detection_errors import DetectionBusyError

            raise DetectionBusyError(self._busy_message)
        return self

    def __exit__(self, *exc_info):
        return False


class FakeWindow:
    """Replay capture frames with their original device timestamps."""

    def __init__(self):
        image = np.zeros((8, 8, 3), dtype=np.uint8)
        self._frames = (Frame(image, 14.0), Frame(image, 14.13))

    def __iter__(self):
        return iter(self._frames)


class FakeCapture:
    def __init__(
        self, connected=False, window="unset", audio=None,
        heart_rate=None, temperature=None,
    ):
        self.connected = connected
        self.window = window
        self.audio = audio
        self._heart_rate = heart_rate
        self._temperature = temperature
        self.audio_calls = []
        self.window_calls = 0
        self.window_seconds = []

    def begin_recording(self):
        return self

    def release_recording(self, recording):
        pass

    def position_ms(self):
        return 1000

    def audio_segment(self, start, end, sample_rate):
        return self.audio if end > start else None

    def is_connected(self):
        return self.connected

    def video_window(self, seconds=None):
        self.window_calls += 1
        self.window_seconds.append(seconds)
        if self.window == "unset":
            return None
        return self.window

    def audio_window(self, seconds=None, *, sample_rate):
        self.audio_calls.append((seconds, sample_rate))
        return self.audio

    def heart_rate_window(self):
        return self._heart_rate

    def temperature_window(self):
        return self._temperature


class FakeMeasurementReader:
    """单一视频生理读取器替身：记录 VideoAnalysisInput 调用。"""

    def __init__(self, value=None, error=None):
        self.value = value
        self.error = error
        self.calls = []

    def result(self, source):
        self.calls.append(source)
        if self.error is not None:
            raise self.error
        return self.value


class FakeSpeech:
    def __init__(self, result=None, error=None):
        self.value = result
        self.error = error
        self.calls = []

    def result(self, audio):
        self.calls.append(audio)
        if self.error is not None:
            raise self.error
        return self.value


class FakeExpression:
    def __init__(self, result=None, error=None):
        self.value = result
        self.error = error
        self.calls = []

    def result_frames(self, frames):
        self.calls.append(frames)
        if self.error is not None:
            raise self.error
        if frames is None:
            return None
        return self.value


class FakeBehavior:
    def __init__(self, result=None, error=None):
        self.value = result
        self.error = error
        self.calls = []

    def result_frames(self, frames):
        self.calls.append(tuple(frames))
        if self.error is not None:
            raise self.error
        return self.value


class FakeFatigueScoring:
    def __init__(self):
        self.calls = []

    def result(self, observation):
        self.calls.append(observation)
        return assess_behavior(observation)


class FakeFatigueAdvice:
    def __init__(self):
        self.calls = []

    def result(self, assessment):
        self.calls.append(assessment)
        return generate_fatigue_advice(assessment)


class FakeTongue:
    def __init__(self, result=None, error=None):
        self.value = result if result is not None else tongue_result_ok()
        self.error = error
        self.calls = []

    def result_frames(self, frames):
        self.calls.append(frames)
        if self.error is not None:
            raise self.error
        if frames is None:
            return None
        return self.value


class FakeHealthQuestions:
    def __init__(self, questions=("1.问题？",)):
        self.questions = tuple(questions)
        self.calls = []

    def __call__(self, fact):
        self.calls.append(fact)
        return HealthQuestionSet(self.questions)


class FakeEmotionQuestions:
    def __init__(self, questions=DEFAULT_PSYCHOLOGY_QUESTIONS):
        self.questions = tuple(questions)
        self.calls = 0

    def result(self):
        self.calls += 1
        return EmotionQuestionSet(self.questions)


def make_ports(**overrides):
    """构造观测网关的五个端口；behavior 替身按组合根姿势包装成观察流。"""
    values = dict(
        admission=FakeAdmission(),
        capture=FakeCapture(),
        expression=FakeExpression(),
        behavior=FakeBehavior(result=valid_behavior()),
        speech=FakeSpeech(),
    )
    values.update(overrides)
    behavior = values.pop("behavior")
    values.setdefault("behavior_observation_flow", BehaviorObservationService(
        capture=values["capture"], behavior=behavior,
    ))
    return GatewayPorts(**values)


def call_handler(handler, *, ports=None, **overrides):
    """以端口字段直接驱动观测处理器（处理器只收端口依赖）。"""
    ports = ports or make_ports()
    values = {field.name: getattr(ports, field.name) for field in dataclasses.fields(ports)}
    values.update(overrides)
    kwargs = {
        name: values[name]
        for name in inspect.signature(handler).parameters
    }
    import asyncio

    return asyncio.run(handler(**kwargs))


def mcu_audio(pcm=b"\x01\x00" * 16000):
    """音频窗口替身：pcm/采样率字段与 AudioData 输入一一对应。"""
    return AudioData(pcm=pcm, sample_rate=16000)


def _client(ports):
    app = make_gateway_app(ports)
    return TestClient(app), app


def _assert_success_envelope(body):
    assert set(body) == {"code", "message", "data"}
    assert body["code"] == 0
    json.loads(json.dumps(body))
    return body


# --- 1. expression observation ---------------------------------------------


def test_expression_observation_serializes_payload_and_admits_once():
    ports = make_ports(
        capture=FakeCapture(window=FakeWindow()),
        expression=FakeExpression(expression_ok()),
    )

    response = _assert_success_envelope(call_handler(
        handle_expression_observation,
        ports=ports,
    ))

    assert response["message"] == "表情识别成功"
    data = response["data"]
    assert set(data) == {"observations", "class_counts"}
    assert data["class_counts"] == [["happy", 1], ["neutral", 1]]
    assert [item["label"] for item in data["observations"]] == ["happy", "neutral"]
    call = ports.admission.calls[0]
    assert (call["key"], call["busy_message"]) == (
        "expression", "表情识别任务正在分析，请勿重复提交",
    )
    assert ports.capture.window_calls == 1
    frames_factory = ports.expression.calls[0]
    assert list(frames_factory()) == [f.data for f in ports.capture.window]


def test_expression_observation_without_video_raises_input_error():
    with pytest.raises(DetectionInputError, match="缺少可用的表情观察"):
        call_handler(
            handle_expression_observation, ports=make_ports(),
        )


def test_expression_observation_propagates_model_runtime_error():
    with pytest.raises(RuntimeError, match="expression inference failed"):
        call_handler(
            handle_expression_observation,
            ports=make_ports(
                capture=FakeCapture(window=FakeWindow()),
                expression=FakeExpression(error=RuntimeError("expression inference failed")),
            ),
        )


def test_empty_capture_observations_raise_input_errors_and_run_no_capability():
    behavior = FakeBehavior()
    ports = make_ports(behavior=behavior)
    for handler in (
        handle_expression_observation,
        handle_behavior_observation,
        handle_speech_observation,
    ):
        with pytest.raises(DetectionInputError):
            call_handler(handler, ports=ports)
    # 采集内存池为空：行为不触碰能力；表情与语音仅以空输入探测一次。
    assert ports.expression.calls == [None]
    assert behavior.calls == []
    assert ports.speech.calls == [None]


# --- 2. behavior observation ------------------------------------------------


def test_behavior_observation_without_video_is_an_input_error():
    with pytest.raises(DetectionInputError, match="暂无已采集的视频画面，请先完成检测"):
        call_handler(
            handle_behavior_observation, ports=make_ports(),
        )


def test_behavior_observation_without_usable_face_frames_is_an_input_error():
    behavior = FakeBehavior(result=None)
    ports = make_ports(
        capture=FakeCapture(window=FakeWindow()),
        behavior=behavior,
    )

    with pytest.raises(DetectionInputError, match="暂无已采集的视频画面，请先完成检测"):
        call_handler(
            handle_behavior_observation,
            ports=ports,
        )
    (samples,) = behavior.calls
    assert [timestamp for timestamp, _ in samples] == [14.0, 14.13]
    for (_, image), frame in zip(samples, ports.capture.window, strict=True):
        assert image is frame.data


def test_behavior_observation_propagates_model_runtime_error():
    """网关不再二次包装：推理异常原样交给外层统一处理。"""
    ports = make_ports(
        capture=FakeCapture(window=FakeWindow()),
        behavior=FakeBehavior(error=RuntimeError("behavior inference failed")),
    )

    with pytest.raises(RuntimeError, match="behavior inference failed"):
        call_handler(
            handle_behavior_observation,
            ports=ports,
        )

    assert ports.admission.calls[0]["key"] == "behavior"


def test_behavior_observation_unexpected_model_error_propagates_to_app_boundary():
    """operation 层不再把未预期异常折进 500 响应；映射集中在 app 级处理器。"""
    ports = make_ports(
        capture=FakeCapture(window=FakeWindow()),
        behavior=FakeBehavior(error=RuntimeError("behavior crash detail")),
    )

    with pytest.raises(RuntimeError, match="behavior crash detail"):
        call_handler(
            handle_behavior_observation,
            ports=ports,
        )


# --- 3. speech observation --------------------------------------------------


def test_speech_observation_returns_segments_only():
    ports = make_ports(
        capture=FakeCapture(audio=mcu_audio()),
        speech=FakeSpeech(Transcript((TranscriptSegment("你好。 最近有点累！"),))),
    )

    response = _assert_success_envelope(call_handler(
        handle_speech_observation,
        ports=ports,
    ))

    assert response["message"] == "语音识别成功"
    assert response["data"] == {"segments": [{"text": "你好。 最近有点累！"}]}
    call = ports.admission.calls[0]
    assert (call["key"], call["busy_message"]) == (
        "speech", "语音识别任务正在分析，请勿重复提交",
    )
    assert ports.speech.calls == [AudioData(pcm=mcu_audio().pcm, sample_rate=16000)]
    assert ports.capture.audio_calls == [(None, 16000)]


def test_speech_observation_propagates_model_runtime_error():
    with pytest.raises(RuntimeError, match="speech inference failed"):
        call_handler(
            handle_speech_observation,
            ports=make_ports(speech=FakeSpeech(error=RuntimeError("speech inference failed"))),
        )


# --- 4. composition ---------------------------------------------------------


def test_create_gateway_ports_composes_task_admission():
    from server.bootstrap import create_gateway_ports

    from server.capture.webrtc.kernel import create_kernel

    ports = create_gateway_ports(
        create_kernel(), TaskAdmission(capacity=1), create_expression_service(),
        create_fatigue_observation_service(), create_speech_service(),
    )

    with ports.admission.acquire("emotion", "busy"):
        pass
    # 准入释放后可再次进入
    with ports.admission.acquire("emotion", "busy"):
        pass


def test_expression_port_gates_unusable_input_at_the_adapter():
    from server.bootstrap import create_gateway_ports

    from server.capture.webrtc.kernel import create_kernel

    class Predictor:
        def recognize_frames(self, frames=None, interval=None):
            return summarize_expressions(())

    expression = create_expression_service()
    expression._predictor_factory = Predictor
    expression.load()
    ports = create_gateway_ports(
        create_kernel(), TaskAdmission(), expression,
        create_fatigue_observation_service(), create_speech_service(),
    )

    assert ports.expression.result_frames(None) is None
    assert ports.expression.result_frames(lambda: iter(())) is None

    ports_without_model = create_gateway_ports(
        create_kernel(), TaskAdmission(), create_expression_service(),
        create_fatigue_observation_service(), create_speech_service(),
    )
    with pytest.raises(RuntimeError, match="ExpressionVideoService is not loaded"):
        ports_without_model.expression.result_frames(lambda: iter(("f0",)))


def test_tongue_adapter_maps_predictor_errors_at_the_domain_boundary():
    from server.capture.webrtc.kernel import create_kernel
    from server.compute.tongue.assembly import create_tongue_observation_service

    class Model:
        def predict_frame(self, frame):
            raise InvalidImageError("无法识别图片格式: /tmp/secret/capture.jpg")

    tongue = create_tongue_recognition_service()
    tongue.model = Model()
    observation = HealthObservationService(
        capture=create_kernel(),
        tongue=create_tongue_observation_service(tongue),
        health_questions=prepare_health_questions,
    )
    # 有纹理的通过帧：噪声保证清晰度阈值通过，质量门放行到达模型
    noisy = np.random.default_rng(3).integers(0, 256, size=(240, 320, 3), dtype=np.uint8)

    # 舌象适配器已下沉到健康域，错误映射仍在适配器边界
    with pytest.raises(DetectionInputError, match="^无法解析上传的图片$") as caught:
        observation.tongue.result_frames(lambda: iter((noisy,)))
    assert isinstance(caught.value.__cause__, InvalidImageError)

    unloaded = HealthObservationService(
        capture=create_kernel(),
        tongue=create_tongue_observation_service(create_tongue_recognition_service()),
        health_questions=prepare_health_questions,
    )
    with pytest.raises(RuntimeError, match="TongueRecognitionService is not loaded"):
        unloaded.tongue.result_frames(lambda: iter((noisy,)))


def test_capture_sensor_windows_summarize_samples_for_the_status_interface():
    """状态接口直接消费采集端口的心率/体温窗口（独立端口包装已删除）。"""
    from server.capture.webrtc.kernel import create_kernel

    kernel = create_kernel()
    assert kernel.heart_rate_window() is None
    assert kernel.temperature_window() is None

    kernel.update_sensors({
        "v": 1, "type": "state", "enabled": True, "run": 1, "audio_base_ms": 0,
        "temperature": {"valid": True, "sampled_at_us": 1, "ambient_c": 24.5,
                        "object_c": 36.2, "error": None},
        "heart_rate": {"valid": True, "sampled_at_us": 1, "bpm": 70.0, "error": None},
    })
    kernel.update_sensors({
        "v": 1, "type": "state", "enabled": True, "run": 1, "audio_base_ms": 0,
        "temperature": {"valid": True, "sampled_at_us": 2, "ambient_c": 24.5,
                        "object_c": 36.2, "error": None},
        "heart_rate": {"valid": True, "sampled_at_us": 2, "bpm": 72.0, "error": None},
    })

    assert kernel.heart_rate_window() == HeartRateWindow(
        samples_bpm=(70.0, 72.0), average_bpm=71.0, latest_bpm=72.0,
        live_samples_bpm=(70.0, 72.0),
    )
    assert kernel.temperature_window() == TemperatureWindow(
        ambient_temps=(24.5, 24.5), object_temps=(36.2, 36.2),
        avg_ambient_temp=24.5, avg_object_temp=36.2,
        latest_ambient_temp=24.5, latest_object_temp=36.2,
    )
