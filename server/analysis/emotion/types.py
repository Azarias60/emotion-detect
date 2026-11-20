"""Emotion question and observation contracts."""

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class EmotionQuestionSet:
    questions: tuple[str, ...]


class EmotionQuestionsPort(Protocol):
    def result(self) -> EmotionQuestionSet: ...
