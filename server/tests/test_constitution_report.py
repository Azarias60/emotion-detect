"""Typed constitution report Result contracts."""

import ast
from dataclasses import asdict
from pathlib import Path

import pytest

from server.compute.tongue.service import ConstitutionReportApplication
from server.compute.tongue.types import ConstitutionAssessment, ConstitutionCandidate
from server.compute.tongue.constitution.report import build_constitution_report
from server.compute.tongue.types import ConstitutionReport


def _rules():
    return {
        "constitutions": {
            "1": {
                "name": "平和质", "english": "Balanced", "category": "normal",
                "key_features": "一行摘要",
                "common_signs": {"eyes": "目光有神", "tongue": "舌淡红", "pulse": "脉平", "sleep": "睡眠好", "empty": ""},
                "psychological": {"summary": "随和", "irritability": 2, "anxiety": 1, "overthinking": 3, "low_mood": 2, "sensitivity": 2},
                "disease_tendency": ["倾向一", "倾向二"],
                "environment": "适应力强",
            },
            "2": {
                "name": "气虚质", "english": "Qi Deficiency", "category": "biased",
                "key_features": "气虚", "common_signs": {"tongue": "有齿痕", "pulse": "脉弱"},
                "psychological": {"summary": "内向"},
                "disease_tendency": [], "environment": "不耐寒",
            },
        },
        "advice": {"1": {"diet": ["a"], "sport": ["b"]}},
        "tongue_rules": [], "constitution_types": {}, "constitution_english": {},
    }


def test_report_result_has_typed_fields_and_preserves_policy():
    result = build_constitution_report(_rules(), 1)
    assert isinstance(result, ConstitutionReport)
    assert asdict(result) == {
        "constitution_id": 1, "constitution_name": "平和质", "constitution_english": "Balanced",
        "category": "正常体质", "one_line": "一行摘要", "body_signals": ("目光有神", "睡眠好"),
        "tongue": "舌淡红", "mind_summary": "随和",
        "mind_highlights": ("易怒/烦躁", "思虑过度", "情绪低落", "敏感多疑"),
        "disease_risk": ("倾向一", "倾向二"), "environment_note": "适应力强",
        "advice": {"diet": ["a"], "sport": ["b"]},
    }


def test_biased_constitution_and_missing_optional_values():
    result = build_constitution_report(_rules(), 2)
    assert result.category == "偏颇体质"
    assert result.body_signals == ()
    assert result.mind_highlights == ()
    assert result.advice == {}
    assert result.tongue == "有齿痕"


@pytest.mark.parametrize("identifier", [0, -1, 999, "unknown"])
def test_unknown_constitution_is_not_encoded_as_an_error_dictionary(identifier):
    with pytest.raises(KeyError, match="未知体质ID"):
        build_constitution_report(_rules(), identifier)


def _assessment(*ids):
    return ConstitutionAssessment(tuple(
        ConstitutionCandidate(id=identifier, name=f"体质{identifier}", english="", weight=1.0, category="normal")
        for identifier in ids
    ))


def test_use_case_reads_rules_from_injected_port_once():
    class Reader:
        calls = 0
        def read(self):
            self.calls += 1
            return _rules()
    reader = Reader()
    result = ConstitutionReportApplication(reader).result(_assessment(1))
    assert result.constitution_name == "平和质"
    assert reader.calls == 1


def test_report_result_none_for_missing_assessment_and_empty_candidates():
    class FailingReader:
        def read(self):
            raise AssertionError("rules must not be read without candidates")

    application = ConstitutionReportApplication(FailingReader())
    assert application.result(None) is None
    assert application.result(ConstitutionAssessment(candidates=())) is None


def test_report_result_projects_the_leading_candidate():
    class Reader:
        def read(self):
            return _rules()

    result = ConstitutionReportApplication(Reader()).result(_assessment(2, 1))

    assert isinstance(result, ConstitutionReport)
    assert result.constitution_id == 2
    assert result.constitution_name == "气虚质"


def test_inner_modules_have_no_outer_imports():
    import server.compute.tongue.service as application
    import server.compute.tongue.constitution.report as report
    for module in (application, report):
        tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
        modules = [node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
        modules += [alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names]
        assert not any(name.startswith(("server.analysis", "server.settings", "server.runtime", "cv2", "torch", "json", "os", "pathlib")) for name in modules)
