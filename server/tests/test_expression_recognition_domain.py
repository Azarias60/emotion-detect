"""Expression payload invariants; no model, HTTP or compatibility dependencies."""

import ast
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from server.compute.expression.types import ExpressionObservation, ExpressionObservations
from server.compute.expression.expression_recognition.domain import summarize_expressions


def test_observations_keep_order_counts_and_unrounded_metadata():
    observations = (
        ExpressionObservation("happy", 0.0, 123.456, 3),
        ExpressionObservation("neutral", 0.777, 124.123, 4),
        ExpressionObservation("happy", 0.5, 124.999, 3),
    )
    result = summarize_expressions(observations)
    assert result == ExpressionObservations(
        observations=observations,
        class_counts=(("happy", 2), ("neutral", 1)),
    )
    assert not hasattr(result, "status")
    with pytest.raises(FrozenInstanceError):
        result.class_counts = ()


def test_empty_samples_produce_an_empty_payload():
    result = summarize_expressions(())
    assert result == ExpressionObservations(observations=(), class_counts=())


def test_result_rejects_counts_that_do_not_match_observations():
    with pytest.raises(ValueError):
        ExpressionObservations(
            (ExpressionObservation("happy", 0.9, 1.0),),
            (("neutral", 1),),
        )


def test_domain_does_not_import_model_or_emotion_analysis():
    import server.compute.expression.expression_recognition.domain as domain
    source = Path(domain.__file__).read_text()
    imports = [node.module for node in ast.walk(ast.parse(source)) if isinstance(node, ast.ImportFrom)]
    assert imports == ["typing", "server.compute.expression.types"]
    from server.compute.expression.types import observations
    contract_imports = [node.module for node in ast.walk(ast.parse(Path(observations.__file__).read_text()))
                        if isinstance(node, ast.ImportFrom)]
    assert contract_imports == ["dataclasses"]
