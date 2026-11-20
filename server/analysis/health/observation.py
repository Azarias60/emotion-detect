"""Health observation that pairs one tongue reading with targeted questions."""

from collections.abc import Callable
from dataclasses import dataclass

from server.analysis.health.types import (
    HealthQuestionSet,
    TongueObservationResult,
)
from server.capture.types import CapturePort
from server.compute.tongue.types import TonguePort, TongueRecognition
from server.concurrency import run_blocking
from server.detection_errors import DetectionInputError


TONGUE_WINDOW_SECONDS = 2.0


@dataclass(frozen=True)
class HealthObservationService:
    capture: CapturePort
    tongue: TonguePort
    health_questions: Callable[[TongueRecognition | None], HealthQuestionSet]

    async def observe_tongue(self) -> TongueObservationResult:
        """一次望诊：2 秒窗口里同时给出舌象事实与据其生成的针对性题目。

        出题不再靠客户端往回喂第二趟识别：题目命题干直接取自本次识别产物，
        两段工作由同一个编排者串行做完，调用方拿到一个不可变组合结果。
        缺画面按输入错误处理；无识别结果时使用通用问诊题目。
        """
        window = self.capture.video_window(TONGUE_WINDOW_SECONDS)
        frames = (lambda: (frame.data for frame in window)) if window is not None else None
        tongue = await run_blocking(self.tongue.result_frames, frames)
        if tongue is None:
            raise DetectionInputError("当前没有可用摄像头画面")
        questions = self.health_questions(tongue.recognition)
        return TongueObservationResult(tongue=tongue, questions=questions)
