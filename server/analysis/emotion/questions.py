"""Emotion question preparation: prompt adapter, parsing and the local bank.

Short or malformed model completions use the historical ten-question default.
The result contains question candidates, not a psychiatric diagnosis; fallback
selection is internal policy and is not exposed on the payload. The LLM reader
is the only transport touchpoint and is bound in ``assembly.py``.
"""

import re
from typing import Protocol

from server.analysis.emotion.types import EmotionQuestionSet
from server.analysis.llm_client import complete_text, request_errors
from server.analysis.prompt_policy import PromptSpec
from server.detection_errors import DetectionUnavailableError, DetectionUpstreamError
from server.logutil import get_logger

logger = get_logger(__name__)


DEFAULT_PSYCHOLOGY_QUESTIONS = (
    "1.最近心情怎么样？", "2.睡眠还好吗？", "3.压力大吗？", "4.有什么烦恼吗？", "5.饮食正常吗？",
    "6.工作顺利吗？", "7.社交多吗？", "8.运动吗？", "9.有爱好吗？", "10.对未来乐观吗？",
)

EMOTION_QUESTION_PROMPT = PromptSpec(
    role="你是情绪问诊问题设计助手。",
    task="面向日常心理健康咨询场景，生成10道循序渐进、适合口头回答的开放式情绪问题。",
    constraints=(
        "覆盖心理状态、压力、生活状态及不同情绪感受。",
        "保持专业、自然，不使用简单的是非题，不预设诊断结论。",
    ),
    output=(
        "只输出10行问题，按1.至10.顺序编号，每行一个。",
        "不要标题、说明、建议或其他前后文。",
    ),
)


def parse_emotion_questions(raw: str) -> EmotionQuestionSet:
    questions = []
    for line in raw.strip().split("\n"):
        line = line.strip()
        if re.match(r"^\d+[\.、]", line):
            questions.append(line)
            if len(questions) == 10:
                break
    if len(questions) < 10:
        return EmotionQuestionSet(DEFAULT_PSYCHOLOGY_QUESTIONS)
    return EmotionQuestionSet(questions=tuple(questions))


class EmotionQuestionTextReader(Protocol):
    def generate(self) -> str: ...


class LLMEmotionQuestionTextReader:
    """与两个报告端口同一装配姿势：传输工厂由 assembly 注入。"""

    def __init__(self, client_factory):
        self._client_factory = client_factory

    def generate(self) -> str:
        with request_errors("情绪问题"):
            client = self._client_factory()
            return complete_text(
                client,
                prompt=EMOTION_QUESTION_PROMPT,
                input_data={"scenario": "常见心理健康咨询"},
                empty_message="问题生成服务返回空内容",
            )


class EmotionQuestionPreparationService:
    """Prepare questions; known AI failures use the business-owned local question bank."""

    def __init__(self, text_reader: EmotionQuestionTextReader):
        self._text_reader = text_reader

    def result(self) -> EmotionQuestionSet:
        try:
            text = self._text_reader.generate()
        except (DetectionUnavailableError, DetectionUpstreamError):
            logger.warning("AI 问题服务不可用，本次使用本地情绪问题", exc_info=True)
            return EmotionQuestionSet(DEFAULT_PSYCHOLOGY_QUESTIONS)
        return parse_emotion_questions(text)
