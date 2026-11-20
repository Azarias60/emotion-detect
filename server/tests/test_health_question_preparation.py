"""Health question preparation consumes the tongue recognition fact."""

from dataclasses import fields

from server.analysis.health.types import HealthQuestionSet
from server.analysis.health.questions import prepare_health_questions
from server.compute.tongue.types import TongueRecognition
from server.compute.tongue.types import TongueDimensionResult


TONGUE = TongueRecognition(
    crack=TongueDimensionResult("normal", 0.9),
    coated=TongueDimensionResult("white", 0.8),
    color=TongueDimensionResult("red", 0.9),
    indentation=TongueDimensionResult("indentation", 0.7),
)


def test_question_set_carries_only_questions():
    payload = HealthQuestionSet(questions=("1. 睡眠？",))
    assert payload.questions == ("1. 睡眠？",)
    assert [field.name for field in fields(HealthQuestionSet)] == ["questions"]
    assert not hasattr(payload, "used_fallback")
    assert not hasattr(payload, "generation_status")


def test_questions_use_current_recognition_fact():
    questions = prepare_health_questions(TONGUE).questions
    assert len(questions) == 5
    assert questions[0] == "本次舌苔识别为白苔，最近是否经常感觉口干、口黏或口中有异味？"
    assert "齿痕舌" in questions[3]


def test_missing_recognition_fact_does_not_invent_tongue_features():
    questions = prepare_health_questions(None).questions
    assert len(questions) == 5
    assert all("识别" not in question for question in questions)
