"""Health report module boundary: typed result values and composition surface."""

from dataclasses import FrozenInstanceError, fields

import pytest

from server.analysis.health.facts import HealthAnswer
from server.analysis.health.reporting.types import (
    HealthReportFacts,
    HealthReportText,
)
from server.analysis.health.reporting.text import HealthReportTextPort
from server.analysis.health.facts import health_answer_fact


def test_report_text_is_the_only_report_result():
    assert [field.name for field in fields(HealthReportText)] == ["text"]
    result = HealthReportText("正文")
    assert result.text == "正文"
    with pytest.raises(FrozenInstanceError):
        result.text = "修改后的文本"


def test_health_answer_is_a_frozen_question_answer_pair():
    answer = HealthAnswer(question="睡眠", answer="一般")
    assert (answer.answer, answer.question) == ("一般", "睡眠")
    assert health_answer_fact(answer) == {"question": "睡眠", "answer": "一般"}
    assert health_answer_fact(HealthAnswer(answer="一般")) == {"answer": "一般"}
    with pytest.raises(FrozenInstanceError):
        answer.answer = "修改"


def test_report_entry_consumes_one_health_fact_and_a_bound_reader():
    import inspect

    init = inspect.signature(HealthReportTextPort.__init__).parameters
    assert set(init) == {"self", "text_reader"}
    entry = inspect.signature(HealthReportTextPort.result).parameters
    assert list(entry) == ["self", "facts"]
    fact_fields = [field.name for field in fields(HealthReportFacts)]
    assert fact_fields[:5] == [
        "tongue_quality", "tongue_recognition", "constitution",
        "constitution_report", "answers",
    ]
