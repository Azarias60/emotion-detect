"""Observation Gateway operations: expression, behavior, and speech.

全部输入来自采集内存窗口快照（行为与望诊窗口由各自观察用例取得）；浏览器上传入口已删除。
本层只做任务准入、调用能力公开入口与结果序列化；失败一律向 app 级异常处理器传播，
能力不可用等已规范化的域异常不在本层再包装。
"""

from server.concurrency import run_blocking
from server.compute.speech.service import ASR_SAMPLE_RATE
from server.compute.speech.types import AudioData
from server.detection_errors import DetectionInputError
from server.presentation.gateway.ports import (
    BehaviorObservationFlowPort,
    CapturePort,
    ExpressionPort,
    SpeechPort,
    TaskAdmissionPort,
)
from server.envelope import success
from server.presentation.gateway.serializers import (
    serialize_behavior_result,
    serialize_expression_result,
    serialize_speech_result,
)


async def handle_expression_observation(
    *,
    admission: TaskAdmissionPort,
    capture: CapturePort,
    expression: ExpressionPort,
) -> dict:
    with admission.acquire("expression", "表情识别任务正在分析，请勿重复提交"):
        window = capture.video_window()
        # capture 统一交付 Frame；表情只要画面，编排层收窄视图（生成器保持流式解码）。
        frames = (lambda: (f.data for f in window)) if window is not None else None
        result = await run_blocking(expression.result_frames, frames)
        if result is None:
            raise DetectionInputError("缺少可用的表情观察，请检查视频与人脸画面")
        return success(serialize_expression_result(result), "表情识别成功")


async def handle_behavior_observation(
    *,
    admission: TaskAdmissionPort,
    behavior_observation_flow: BehaviorObservationFlowPort,
) -> dict:
    with admission.acquire("behavior", "行为识别任务正在分析，请勿重复提交"):
        result = await behavior_observation_flow.observe_behavior()
        return success(serialize_behavior_result(result), "行为识别成功")


async def handle_speech_observation(
    *,
    admission: TaskAdmissionPort,
    capture: CapturePort,
    speech: SpeechPort,
) -> dict:
    with admission.acquire("speech", "语音识别任务正在分析，请勿重复提交"):
        audio_window = capture.audio_window(sample_rate=ASR_SAMPLE_RATE)
        audio = (
            AudioData(pcm=audio_window.pcm, sample_rate=audio_window.sample_rate)
            if audio_window is not None else None
        )
        result = await run_blocking(speech.result, audio)
        if result is None:
            raise DetectionInputError("缺少可用的语音转写，请检查音频输入")
        return success(serialize_speech_result(result), "语音识别成功")


__all__ = [
    "handle_behavior_observation",
    "handle_expression_observation",
    "handle_speech_observation",
]
