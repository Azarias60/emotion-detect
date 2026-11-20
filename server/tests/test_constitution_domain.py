"""Pure constitution rule-matching policy, independent of file/model access."""

from dataclasses import FrozenInstanceError, asdict

import pytest

from server.compute.tongue.constitution.adapters.json_rules import JsonConstitutionRulesReader
from server.compute.tongue.constitution.domain import infer_constitution_candidates
from server.compute.tongue.types import ConstitutionCandidate


@pytest.fixture
def rules():
    return {
        "tongue_rules": [
            ["red", "white", "no", "yes", [[2, 0.2], [1, 0.9], [3, 0.9]]],
            ["red", "white", "no", "yes", [[4, 1.0]]],
            ["pale", "white", "no", "no", []],
        ],
        "constitution_types": {"1": "平和质", "2": "气虚质"},
        "constitution_english": {"1": "Balanced"},
        "constitutions": {"1": {"category": "normal"}, "2": {"category": "biased"}},
    }


def test_rule_order_ties_and_unknown_fallback(rules):
    result = infer_constitution_candidates(
        rules, color_class="red", coated_class="white", crack_class="no", indent_class="yes",
    )
    assert isinstance(result, tuple)
    assert all(isinstance(item, ConstitutionCandidate) for item in result)
    assert [asdict(item) for item in result] == [
        {"id": 1, "name": "平和质", "english": "Balanced", "weight": 0.9, "category": "normal"},
        {"id": 3, "name": "未知3", "english": "", "weight": 0.9, "category": "unknown"},
        {"id": 2, "name": "气虚质", "english": "", "weight": 0.2, "category": "biased"},
    ]
    assert rules["tongue_rules"][0][4] == [[2, 0.2], [1, 0.9], [3, 0.9]]


@pytest.mark.parametrize("features", [
    ("other", "white", "no", "yes"),
    ("pale", "white", "no", "no"),
])
def test_no_match_or_empty_rule_returns_empty_tuple(rules, features):
    assert infer_constitution_candidates(
        rules, color_class=features[0], coated_class=features[1],
        crack_class=features[2], indent_class=features[3],
    ) == ()


def test_candidate_is_immutable(rules):
    result = infer_constitution_candidates(
        rules, color_class="red", coated_class="white", crack_class="no", indent_class="yes",
    )[0]
    with pytest.raises(FrozenInstanceError):
        result.name = "changed"


def test_real_rule_table_retains_first_match_order_and_labels():
    data = JsonConstitutionRulesReader().read()
    for entry in data["tongue_rules"]:
        key = tuple(entry[:4])
        first_match = next((row[4] for row in data["tongue_rules"] if tuple(row[:4]) == key), [])
        expected = [
            ConstitutionCandidate(
                id=cid,
                name=data["constitution_types"].get(str(cid), f"未知{cid}"),
                english=data["constitution_english"].get(str(cid), ""),
                weight=weight,
                category=data["constitutions"].get(str(cid), {}).get("category", "unknown"),
            )
            for cid, weight in sorted(first_match, key=lambda pair: -pair[1])
        ]
        assert infer_constitution_candidates(
            data, color_class=key[0], coated_class=key[1], crack_class=key[2], indent_class=key[3],
        ) == tuple(expected)
