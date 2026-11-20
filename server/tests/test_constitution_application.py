"""Constitution use-case contracts: a typed Result and inward rule-reader port."""

import ast
import json
from dataclasses import asdict
from pathlib import Path

import pytest

from server.compute.tongue.constitution.adapters.json_rules import JsonConstitutionRulesReader
from server.compute.tongue.service import ConstitutionAssessmentApplication
from server.compute.tongue.types import ConstitutionAssessment, ConstitutionCandidate
from server.compute.tongue.types import TongueDimensionResult, TongueRecognition


@pytest.fixture
def rules():
    return {
        "tongue_rules": [
            ["red", "white", "no", "yes", [[2, 0.2], [1, 0.9], [3, 0.9]]],
            ["red", "white", "no", "yes", [[4, 1.0]]],
        ],
        "constitution_types": {"1": "平和质", "2": "气虚质"},
        "constitution_english": {"1": "Balanced"},
        "constitutions": {"1": {"category": "normal"}, "2": {"category": "biased"}},
        "advice": {},
    }


def recognition():
    return TongueRecognition(
        crack=TongueDimensionResult("no", 0.9),
        coated=TongueDimensionResult("white", 0.8),
        color=TongueDimensionResult("red", 0.9),
        indentation=TongueDimensionResult("yes", 0.7),
    )


def test_use_case_consumes_recognition_result_through_injected_port(rules):
    class Reader:
        def __init__(self):
            self.calls = 0
        def read(self):
            self.calls += 1
            return rules

    reader = Reader()
    result = ConstitutionAssessmentApplication(reader).result(recognition())
    assert reader.calls == 1
    assert isinstance(result, ConstitutionAssessment)
    assert all(isinstance(item, ConstitutionCandidate) for item in result.candidates)
    assert [asdict(item) for item in result.candidates] == [
        {"id": 1, "name": "平和质", "english": "Balanced", "weight": 0.9, "category": "normal"},
        {"id": 3, "name": "未知3", "english": "", "weight": 0.9, "category": "unknown"},
        {"id": 2, "name": "气虚质", "english": "", "weight": 0.2, "category": "biased"},
    ]
    assert rules["tongue_rules"][0][4] == [[2, 0.2], [1, 0.9], [3, 0.9]]


def test_no_matching_rule_is_an_empty_typed_result(rules):
    result = ConstitutionAssessmentApplication(Reader(rules)).result(
        TongueRecognition(
            crack=TongueDimensionResult("no", 0.9),
            coated=TongueDimensionResult("white", 0.8),
            color=TongueDimensionResult("other", 0.9),
            indentation=TongueDimensionResult("yes", 0.7),
        )
    )
    assert result == ConstitutionAssessment(candidates=())


def test_missing_recognition_returns_none_without_touching_the_reader():
    class FailingReader:
        def read(self):
            raise AssertionError("rules must not be read without recognition")

    assert ConstitutionAssessmentApplication(FailingReader()).result(None) is None


class Reader:
    def __init__(self, data):
        self.data = data
    def read(self):
        return self.data


def test_rule_reader_failure_propagates():
    class BrokenReader:
        def read(self):
            raise OSError("missing rules")
    with pytest.raises(OSError, match="missing rules"):
        ConstitutionAssessmentApplication(BrokenReader()).result(recognition())


def test_json_adapter_reads_the_supplied_path(tmp_path):
    path = tmp_path / "rules.json"
    path.write_text(json.dumps({"constitution_types": {"1": "平和质"}}), encoding="utf-8")
    assert JsonConstitutionRulesReader(path).read() == {"constitution_types": {"1": "平和质"}}


def test_inner_use_case_and_port_do_not_depend_on_outer_layers():
    import server.compute.tongue.service as application
    import server.compute.tongue.constitution.ports as ports
    for module in (application, ports):
        source = Path(module.__file__).read_text(encoding="utf-8")
        imports = [node for node in ast.walk(ast.parse(source)) if isinstance(node, (ast.Import, ast.ImportFrom))]
        names = [alias.name for node in imports for alias in node.names] + [
            node.module or "" for node in imports if isinstance(node, ast.ImportFrom)
        ]
        assert not any(name.startswith((
            "server.analysis", "server.runtime", "server.settings", "cv2", "numpy", "json", "os", "pathlib",
        )) for name in names)
