# -*-coding: utf-8 -*-
"""设计 001 首批契约基线使用的可控替身与测试素材。

替身边界（避免真实模型 / LLM / 硬件参与）：
- 表情识别模型（face_detection create_predictor 的返回值）
- KDE-HRV 与疲劳行为分析（detection_models 两个分析入口）
- 语音识别模型（funasr AutoModel.generate）
- 舌象四维权重推理（TonguePredictor.predict）
- OpenAI 客户端（generate_* 报告/问题函数中的 LLM 调用）

保持真实：FastAPI 路由与表单解析、ffmpeg 转码、图片质量检查、舌象标签映射、
体质规则、语音文本分句、疲劳建议分级、HRV 汇总、锁与临时目录清理。
"""
import io
import math
import os
import tempfile
import types
import wave

import cv2
import numpy as np
from server.compute.tongue.tongue_recognition.errors import TonguePredictorError
from server.compute.expression.types import ExpressionObservation
from server.compute.expression.expression_recognition.domain import summarize_expressions


def unconfigured(name):
    """未在用例中配置返回值的替身直接失败，防止测试静默通过。"""

    def _fail(*args, **kwargs):
        raise AssertionError(f"测试未配置替身 {name} 的返回值；请用夹具注入受控结果")

    return _fail


class StubTonguePredictorError(TonguePredictorError):
    """与真实 TonguePredictorError 同为异常基类的替身。"""


class StubTonguePredictor:
    default_result = {
        "color": {"class": "red", "probability": 0.91},
        "coated": {"class": "white", "probability": 0.82},
        "crack": {"class": "normal", "probability": 0.87},
        "indentation": {"class": "indentation", "probability": 0.66},
    }

    def __init__(self, device=None):
        self.result = {key: dict(value) for key, value in self.default_result.items()}
        self.error_to_raise = None
        self.predict_calls = []

    def predict(self, image_path):
        self.predict_calls.append(image_path)
        if self.error_to_raise is not None:
            raise self.error_to_raise
        return {key: dict(value) for key, value in self.result.items()}

    def predict_frame(self, frame):
        self.predict_calls.append(frame)
        if self.error_to_raise is not None:
            raise self.error_to_raise
        return {key: dict(value) for key, value in self.result.items()}


class StubFacePredictor:
    """Typed expression-recognition test double with deterministic observations."""

    def __init__(self, emotions=("happy", "neutral")):
        self.result = summarize_expressions(tuple(
            ExpressionObservation(label=emotion, confidence=0.9, timestamp=float(index), class_index=None)
            for index, emotion in enumerate(emotions)
        ))
        self.recognize_video_calls = []

    def recognize_video(self, video_file=None, interval=None):
        self.recognize_video_calls.append(
            {
                "video_file": video_file,
                "interval": interval,
                "exists": os.path.exists(video_file) if video_file else None,
            }
        )
        return self.result

    def recognize_frames(self, frames=None, interval=None):
        self.recognize_video_calls.append(
            {"frames": list(frames()) if frames is not None else None, "interval": interval},
        )
        return self.result


class StubVoiceModel:
    """语音识别替身：generate 返回与 funasr 相同结构的单段结果。"""

    def __init__(self, raw_text):
        self.raw_text = raw_text
        self.generate_calls = []

    def generate(self, input=None, input_format=None, fs=None, language=None, use_itn=True):
        self.generate_calls.append({"input": input, "input_format": input_format, "fs": fs})
        return [{"text": self.raw_text}]


class _FakeCompletions:
    def __init__(self, owner):
        self._owner = owner

    def create(self, **kwargs):
        self._owner.calls.append(kwargs)
        message = types.SimpleNamespace(content=self._owner.reply)
        choice = types.SimpleNamespace(message=message)
        return types.SimpleNamespace(choices=[choice])


class FakeOpenAI:
    """OpenAI 客户端替身：固定回复并记录每次请求参数，不发网络请求。"""

    reply = ""
    instances = []

    def __init__(self, base_url=None, api_key=None, **kwargs):
        self.base_url = base_url
        self.api_key = api_key
        self.calls = []
        type(self).instances.append(self)
        self.chat = types.SimpleNamespace(completions=_FakeCompletions(self))


def make_analysis_stub(result=None, error=None):
    """构造视频分析入口的替身；路径（冻结上传）与帧工厂（设备）分别记录。"""
    calls = []

    def _stub(video_path, model_path=None):
        entry = (
            {"frames": video_path, "model_path": model_path}
            if callable(video_path) and not isinstance(video_path, str)
            else {"video_path": video_path, "model_path": model_path}
        )
        calls.append(entry)
        if error is not None:
            raise error
        return result

    _stub.calls = calls
    return _stub



def build_jpeg_bytes(size=(320, 240), brightness=160):
    width, height = size
    frame = np.full((height, width, 3), brightness, dtype=np.uint8)
    frame[10:40, 10:60] = 40  # 加入对比区域，避免质量检查结果全部依赖同一特征
    ok, buf = cv2.imencode(".jpg", frame)
    assert ok, "测试图片编码失败"
    return buf.tobytes()


