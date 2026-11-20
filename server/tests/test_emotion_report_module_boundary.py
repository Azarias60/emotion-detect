"""M10: emotion report result contract, prompt rebuild and statistics parser."""
import ast
import json
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace

from server import settings
from server.analysis.emotion.reporting.text import OpenAIEmotionReportTextReader
from server.analysis.emotion.reporting.types import (
    EmotionAnswer,
    EmotionReportFacts,
    EmotionReportText,
)
from server.analysis.emotion.reporting.flow import extract_emotion_count_from_report
from server.compute.expression.types import ExpressionObservation, ExpressionObservations
from server.compute.fatigue.types import FatigueAssessment
from server.compute.speech.types import Transcript, TranscriptSegment


def test_report_has_single_canonical_execution_entry():
    from server.analysis.emotion.assembly import create_emotion_report_port
    port = create_emotion_report_port()
    assert callable(port.result)
    assert extract_emotion_count_from_report("喜怒哀虑统计：喜:1 怒:2 哀:3 虑:4") == {
        "喜": 1, "怒": 2, "哀": 3, "虑": 4,
    }


def test_report_port_rebuilds_prompt_from_named_capability_values():
    sent = []
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(
            content="  情绪报告正文\n最终判定：平静\n喜怒哀虑统计：喜:1 怒:0 哀:2 虑:3\n",
        ))]
    )
    client = SimpleNamespace(chat=SimpleNamespace(
        completions=SimpleNamespace(create=lambda **kwargs: (sent.append(kwargs), response)[1])
    ))
    factory_calls = []
    def factory():
        factory_calls.append(True)
        return client

    reader = OpenAIEmotionReportTextReader(factory)
    assert factory_calls == []
    output = reader.result(EmotionReportFacts(
        expression=asdict(ExpressionObservations(
            (ExpressionObservation("happy", 0.9, 0.5),), (("happy", 1),),
        )),
        behavior=None,
        fatigue=asdict(FatigueAssessment(score=1, level="正常")),
        advice={"level": "轻度疲劳", "text": "建议短暂远眺和伸展，调整坐姿，并在下一工作周期安排休息。"},
        answers=(EmotionAnswer(question="最近心情怎么样？", answer="还不错"),),
        speech={"segments": [{"text": "你好"}]},
        physiology=None,
    ))
    assert factory_calls == [True]
    assert output == EmotionReportText("情绪报告正文\n最终判定：平静\n喜怒哀虑统计：喜:1 怒:0 哀:2 虑:3")
    assert len(sent) == 1
    call = sent[0]
    assert call["model"] == settings.AI_CONFIG["model"]
    assert call["temperature"] == settings.AI_CONFIG["temperature"]
    assert call["max_tokens"] == settings.AI_CONFIG["max_tokens"]
    prompt = call["messages"][0]["content"]
    assert "最终判定：开心" in prompt
    assert "不要做疾病" not in prompt
    assert "喜怒哀虑统计：喜:X 怒:X 哀:X 虑:X" in prompt
    # data_status 词汇已随结果契约迁移从提示词中移除
    assert "data_status" not in prompt
    assert "值为 null 的指标" in prompt
    assert call["messages"][1]["role"] == "user"
    assert [message["role"] for message in call["messages"]] == ["system", "user"]
    submitted = json.loads(call["messages"][1]["content"])
    assert set(submitted) == {
        "expression", "behavior", "fatigue", "advice", "answers", "speech",
        "physiology",
    }
    assert submitted["expression"] == {
        "observations": [
            {"label": "happy", "confidence": 0.9, "timestamp": 0.5, "class_index": None},
        ],
        "class_counts": [["happy", 1]],
    }
    assert submitted["speech"] == {"segments": [{"text": "你好"}]}
    assert submitted["fatigue"] == {"score": 1, "level": "正常"}
    assert submitted["advice"]["level"] == "轻度疲劳"
    assert submitted["behavior"] is None
    assert submitted["physiology"] is None


def test_report_failure_propagates_and_word_count_parsing_is_preserved():
    def unavailable():
        raise RuntimeError("service offline")

    import pytest


    with pytest.raises(RuntimeError, match="service offline"):
        OpenAIEmotionReportTextReader(unavailable).result(EmotionReportFacts(
            expression=None, behavior=None, fatigue=None,
            advice={"level": "数据不足", "text": "行为数据不足，无法判断疲劳程度；其他可用指标仍已完成分析。"},
            answers=(), speech=None, physiology=None,
        ))

    assert extract_emotion_count_from_report("无统计行") == {"喜": 0, "怒": 0, "哀": 0, "虑": 0}
    assert extract_emotion_count_from_report(
        "喜怒哀虑统计：喜:7 怒:3 哀:2 虑:10"
    ) == {"喜": 7, "怒": 3, "哀": 2, "虑": 10}



def test_report_module_does_not_import_foreign_impl():
    import server.analysis.emotion.reporting.text as reporting_module

    imports = [
        node.module for node in ast.walk(
            ast.parse(Path(reporting_module.__file__).read_text(encoding="utf-8"))
        ) if isinstance(node, ast.ImportFrom) and node.module
    ]
    assert not any(name.startswith("server.emotion_detection") for name in imports)
    assert not any(name.startswith("server.presentation.gateway") for name in imports)
    assert not any(name.startswith("server.analysis.emotion.assembly") for name in imports)
    server_dir = Path(reporting_module.__file__).resolve().parents[2]
    assert not (server_dir / "analysis" / "emotion" / "llm.py").exists()
