"""Question preparation as an independent capability returning a EmotionQuestionSet."""

import ast
import pytest
from dataclasses import fields
from pathlib import Path

from server.analysis.emotion.questions import EmotionQuestionPreparationService
from server.analysis.emotion.questions import (
    DEFAULT_PSYCHOLOGY_QUESTIONS, parse_emotion_questions,
)
from server.analysis.emotion.types import EmotionQuestionSet


def test_question_set_carries_only_questions():
    payload = EmotionQuestionSet(questions=DEFAULT_PSYCHOLOGY_QUESTIONS)
    assert [field.name for field in fields(EmotionQuestionSet)] == ["questions"]
    assert not hasattr(payload, "generation_status")
    assert not hasattr(payload, "used_fallback")


def test_first_ten_numbered_lines_are_preserved():
    text = "header\n" + "\n".join(f"{n}. 问题{n}" for n in range(1, 13))
    result = parse_emotion_questions(text)
    assert isinstance(result, EmotionQuestionSet)
    assert result.questions == tuple(f"{n}. 问题{n}" for n in range(1, 11))
    assert not hasattr(result, "generation_status")


def test_short_answer_uses_unmodified_default_questions():
    result = parse_emotion_questions("1. 只有一道题\n2、还有一道题")
    assert result == EmotionQuestionSet(questions=DEFAULT_PSYCHOLOGY_QUESTIONS)
    assert not hasattr(result, "generation_status")


def test_injected_reader_does_not_require_llm_or_settings():
    class FakeReader:
        def __init__(self): self.calls = 0
        def generate(self):
            self.calls += 1
            return "\n".join(f"{n}、问题" for n in range(1, 11))
    reader = FakeReader()
    assert len(EmotionQuestionPreparationService(reader).result().questions) == 10
    assert reader.calls == 1


def test_inner_layers_have_no_http_framework_and_service_builds_no_transport():
    """合并后的单文件里按类守护：模块无 HTTP 框架依赖，服务类不构造传输零件。"""
    import server.analysis.emotion.questions as questions

    source = Path(questions.__file__).read_text()
    assert "fastapi" not in source.lower()

    tree = ast.parse(source)
    service = next(
        node for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef) and node.name == "EmotionQuestionPreparationService"
    )
    transported = [
        node for node in ast.walk(service)
        if isinstance(node, ast.Name) and node.id in ("OpenAI", "create_client")
        or isinstance(node, ast.Attribute) and node.attr in ("chat", "completions")
    ]
    assert transported == []


def test_reader_failure_falls_back_to_default_question_set():
    class BrokenReader:
        def generate(self):
            from server.detection_errors import DetectionUnavailableError
            raise DetectionUnavailableError("no model")

    result = EmotionQuestionPreparationService(BrokenReader()).result()
    assert isinstance(result, EmotionQuestionSet)
    assert result.questions == DEFAULT_PSYCHOLOGY_QUESTIONS
    assert not hasattr(result, "generation_status")


def test_question_use_case_is_composed_outside_emotion_analysis():
    """问题准备由分析装配组合，检测执行链经业务调度消费，无 app.state 聚合。"""
    from server.analysis.emotion.assembly import create_emotion_question_service
    from server.app import create_app
    from server.tests.runtime_fixture import assembly
    app = create_app(assembly)
    assert isinstance(create_emotion_question_service(), EmotionQuestionPreparationService)
    assert not hasattr(app.state, "use_cases")


@pytest.mark.parametrize("failure", [TypeError("bug"), ValueError("bad contract"), RuntimeError("internal defect")])
def test_programming_errors_never_select_local_questions(failure):
    class BrokenReader:
        def generate(self):
            raise failure
    with pytest.raises(type(failure), match=str(failure)):
        EmotionQuestionPreparationService(BrokenReader()).result()
