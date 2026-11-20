"""health reporting owns the prompt, payload port and OpenAI reader; assembly owns the wiring."""

import ast
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from server import settings
from server.analysis.health.reporting.text import OpenAIHealthReportTextReader


def test_report_prompt_sends_named_payload_and_health_constraints():
    calls = []
    factory_calls = []
    answer = SimpleNamespace(choices=[SimpleNamespace(
        message=SimpleNamespace(content="  原始报告正文\n"),
    )])
    client = SimpleNamespace(chat=SimpleNamespace(
        completions=SimpleNamespace(create=lambda **kwargs: (calls.append(kwargs), answer)[1]),
    ))
    def factory():
        factory_calls.append(True)
        return client

    payload = {
        "tongue_quality": {"passed": True},
        "questions": ["最近的睡眠怎么样？"],
        "answers": [{"question": "睡眠", "answer": "睡眠不好"}],
        "speech": {"segments": [{"text": "困倦"}]},
        "physiology": None,
    }
    adapter = OpenAIHealthReportTextReader(factory)
    assert factory_calls == []
    assert adapter.generate(payload) == "原始报告正文"
    assert factory_calls == [True]
    assert len(calls) == 1
    call = calls[0]
    assert call["model"] == settings.AI_CONFIG["model"]
    assert call["temperature"] == settings.AI_CONFIG["temperature"]
    assert call["max_tokens"] == settings.AI_CONFIG["max_tokens"]
    system, user = call["messages"]
    assert system["role"] == "system"
    assert user["role"] == "user"
    assert json.loads(user["content"]) == payload
    assert "睡眠不好" in user["content"]
    assert "睡眠不好" not in system["content"]
    for rule in (
        "不得自行配给问题",
        "重复的内容只计一次，不得编造回答",
        "不得做疾病确诊",
        "缺失的信息只作为数据不足处理",
        "不能替代专业医疗诊断",
    ):
        assert rule in system["content"]


def test_reader_does_not_replace_transport_failures_or_invent_fallback():
    def broken_factory():
        raise RuntimeError("service offline")

    reader = OpenAIHealthReportTextReader(broken_factory)
    with pytest.raises(RuntimeError, match="service offline"):
        reader.generate({})


def test_owned_reporting_module_has_no_foreign_imports_and_assembly_owns_the_wiring():
    import server.analysis.health.reporting.text as reporting_module

    owned = ast.parse(Path(reporting_module.__file__).read_text(encoding="utf-8"))
    imports = [node.module for node in ast.walk(owned)
               if isinstance(node, ast.ImportFrom) and node.module]
    assert not any(name.startswith("server.presentation") for name in imports)
    assert not any(name.startswith("server.coordination") for name in imports)

    server_dir = Path(reporting_module.__file__).resolve().parents[2]
    assert not (server_dir / "analysis" / "health" / "llm.py").exists()
    source = Path(reporting_module.__file__).read_text(encoding="utf-8")
    assert "self._text_reader.generate(payload)" in source
    import server.analysis.health.assembly as factory_module
    wiring = Path(factory_module.__file__).read_text(encoding="utf-8")
    assert "OpenAIHealthReportTextReader(create_client)" in wiring
