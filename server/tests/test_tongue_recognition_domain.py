"""Contract-first gates for the independent TongueRecognition result."""

import ast
from dataclasses import FrozenInstanceError, asdict
from pathlib import Path

import pytest

from server.compute.tongue.types import TongueDimensionResult, TongueRecognition
from server.compute.tongue.tongue_recognition.domain import build_tongue_recognition


@pytest.fixture
def predictions():
    # The model currently returns class/probability per dimension; keep the
    # four model outputs independent of label localization and constitution.
    return {
        "crack": {"class": "normal", "probability": 0.75},
        "coated": {"class": "white", "probability": 0.51},
        "color": {"class": "red", "probability": 0.0},
        "indentation": {"class": "indentation", "probability": 1.0},
    }


def test_four_dimensions_preserve_model_values(predictions):
    result = build_tongue_recognition(predictions)
    assert isinstance(result, TongueRecognition)
    assert all(isinstance(getattr(result, name), TongueDimensionResult) for name in predictions)
    for dimension, prediction in predictions.items():
        assert asdict(getattr(result, dimension)) == {
            "class_name": prediction["class"],
            "probability": prediction["probability"],
        }


def test_zero_probability_is_not_treated_as_missing(predictions):
    result = build_tongue_recognition(predictions)
    assert result.color.probability == 0.0


def test_projection_does_not_mutate_model_result(predictions):
    snapshot = {key: dict(value) for key, value in predictions.items()}
    build_tongue_recognition(predictions)
    assert predictions == snapshot


def test_result_and_each_dimension_are_immutable(predictions):
    result = build_tongue_recognition(predictions)
    with pytest.raises(FrozenInstanceError):
        result.color = result.coated
    with pytest.raises(FrozenInstanceError):
        result.color.class_name = "white"


@pytest.mark.parametrize("missing", ["crack", "coated", "color", "indentation"])
def test_missing_model_dimension_is_not_silently_defaulted(predictions, missing):
    predictions.pop(missing)
    with pytest.raises(KeyError, match=missing):
        build_tongue_recognition(predictions)


def test_domain_does_not_import_model_runtime_or_health_adapter():
    import server.compute.tongue.tongue_recognition.domain as domain
    source = Path(domain.__file__).read_text(encoding="utf-8")
    imports = [node for node in ast.walk(ast.parse(source)) if isinstance(node, (ast.Import, ast.ImportFrom))]
    names = [alias.name for node in imports for alias in node.names] + [
        node.module or "" for node in imports if isinstance(node, ast.ImportFrom)
    ]
    assert not any(name.startswith((
        "server.analysis", "server.runtime", "server.engines", "server.vendor",
        "cv2", "torch", "torchvision", "PIL", "fastapi",
    )) for name in names)
