"""能力网关的 HTTP 契约 DTO：与真实输出逐字段一致。

这些模型是 OpenAPI 契约的人工维护来源；检测意图路由只提交模式、
使用条件与展示反馈，检测事实经报告观察传输提供，不走请求响应。
"""
from typing import Literal

from pydantic import BaseModel, ConfigDict, with_config

from server.analysis.emotion.facts import ExpressionFact
from server.analysis.facts import (
    BehaviorObservationFact,
    SpeechFact,
)


class StrictModel(BaseModel):
    """契约模型：禁止未声明字段，杜绝静默过滤或补字段。"""

    model_config = ConfigDict(extra="forbid")


# --- 检测意图 -----------------------------------------------------------------


class DetectionIntent(StrictModel):
    """启动检测：前端只提交模式，准备、采集与问答由后端安排。"""

    kind: Literal["health", "fatigue", "emotion"]


class DetectionReportId(StrictModel):
    reportId: int


class UsageCondition(StrictModel):
    """检测页面的使用条件；mode 为空表示页面使用期结束。"""

    mode: Literal["health", "fatigue", "emotion"] | None


class QuestionIndex(StrictModel):
    index: int


# --- 观测 ---------------------------------------------------------------------


@with_config(ConfigDict(extra="forbid"))
class ExpressionResult(ExpressionFact):
    pass


@with_config(ConfigDict(extra="forbid"))
class BehaviorResult(BehaviorObservationFact):
    pass


@with_config(ConfigDict(extra="forbid"))
class SpeechResult(SpeechFact):
    pass


# --- 采集状态 -------------------------------------------------------------------


class AudioLevelEnvelope(StrictModel):
    levels: list[float]
    step_ms: int
    age_ms: int | None
    duration_ms: int


class CaptureStatusPayload(StrictModel):
    connected: bool
    frame_available: bool
    audio: AudioLevelEnvelope


__all__ = [
    "AudioLevelEnvelope",
    "BehaviorResult",
    "CaptureStatusPayload",
    "DetectionIntent",
    "DetectionReportId",
    "ExpressionResult",
    "QuestionIndex",
    "SpeechResult",
    "UsageCondition",
]
