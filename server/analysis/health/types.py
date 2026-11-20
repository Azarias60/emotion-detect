"""Health question and observation contracts."""

from dataclasses import dataclass
from typing import Protocol

from server.compute.tongue.types import TongueResult


@dataclass(frozen=True)
class HealthQuestionSet:
    questions: tuple[str, ...]


@dataclass(frozen=True)
class TongueObservationResult:
    """Tongue findings and targeted questions from the same observation."""

    tongue: TongueResult
    questions: HealthQuestionSet


class HealthObservationFlowPort(Protocol):
    """无帧入的望诊编排：自己取最近 2 秒窗口，返回舌象事实与题目的不可变组合。

    选帧与质量判定由舌象域完成，健康域使用同一识别结果生成问题；
    未确认可用画面抛 DetectionInputError，未识别舌象时使用通用问题。
    任务准入属于调用方，结果不跨请求保存。
    """

    async def observe_tongue(self) -> TongueObservationResult: ...
