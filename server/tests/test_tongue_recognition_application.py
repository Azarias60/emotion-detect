"""TongueRecognition Port inversion and model-lock adapter regression gates."""

import ast
import threading
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace

import pytest

from server.compute.tongue.service import TongueRecognitionApplication
from server.compute.tongue.types import TongueRecognition
from server.compute.tongue.service import LockedTonguePredictionReader


@pytest.fixture
def predictions():
    return {
        "color": {"class": "red", "probability": 0.91},
        "coated": {"class": "white", "probability": 0.82},
        "crack": {"class": "normal", "probability": 0.0},
        "indentation": {"class": "indentation", "probability": 0.66},
    }


def test_use_case_calls_injected_reader_once_and_returns_typed_result(predictions):
    class FakeReader:
        def __init__(self):
            self.calls = []
        def is_available(self):
            return True
        def predict(self, path):
            self.calls.append(path)
            return predictions
    reader = FakeReader()
    result = TongueRecognitionApplication(reader).result("sample.jpg")
    assert isinstance(result, TongueRecognition)
    assert reader.calls == ["sample.jpg"]
    assert asdict(result) == {
        "crack": {"class_name": "normal", "probability": 0.0},
        "coated": {"class_name": "white", "probability": 0.82},
        "color": {"class_name": "red", "probability": 0.91},
        "indentation": {"class_name": "indentation", "probability": 0.66},
    }


def test_use_case_propagates_reader_failure():
    class BrokenReader:
        def is_available(self):
            return True
        def predict(self, path):
            raise ValueError("invalid image")
    with pytest.raises(ValueError, match="invalid image"):
        TongueRecognitionApplication(BrokenReader()).result("broken.png")


@pytest.mark.parametrize("missing", ["color", "coated", "crack", "indentation"])
def test_use_case_does_not_default_missing_dimensions(predictions, missing):
    del predictions[missing]
    with pytest.raises(KeyError, match=missing):
        TongueRecognitionApplication(SimpleNamespace(is_available=lambda: True, predict=lambda path: predictions)).result("x")


def test_use_case_and_port_import_no_outer_dependencies():
    import server.compute.tongue.service as application
    import server.compute.tongue.tongue_recognition.ports as ports
    for module in (application, ports):
        source = Path(module.__file__).read_text(encoding="utf-8")
        imports = [node for node in ast.walk(ast.parse(source)) if isinstance(node, (ast.Import, ast.ImportFrom))]
        names = [alias.name for node in imports for alias in node.names] + [
            node.module or "" for node in imports if isinstance(node, ast.ImportFrom)
        ]
        assert not any(name.startswith((
            "server.analysis", "server.runtime", "server.engines", "server.vendor",
            "server.compute.tongue.tongue_recognition.adapters", "torch", "cv2", "fastapi",
        )) for name in names)


def test_adapter_holds_model_lock_during_inference(predictions):
    lock = threading.Lock()
    calls = []
    class Model:
        def predict(self, path):
            assert lock.locked()
            calls.append(path)
            return predictions
    model = Model()
    result = TongueRecognitionApplication(LockedTonguePredictionReader(
        tongue_model=lambda: model, tongue_model_lock=lambda: lock,
    )).result("capture.jpg")
    assert result.color.class_name == "red"
    assert calls == ["capture.jpg"]
    assert not lock.locked()


def test_adapter_missing_model_is_lifecycle_error():
    with pytest.raises(RuntimeError, match="TongueRecognitionService is not loaded"):
        LockedTonguePredictionReader(
            tongue_model=lambda: None, tongue_model_lock=threading.Lock,
        ).predict("capture.jpg")


def test_adapter_releases_lock_after_inference_error():
    lock = threading.Lock()
    class Model:
        def predict(self, path):
            assert lock.locked()
            raise ValueError("model failure")
    model = Model()
    with pytest.raises(ValueError, match="model failure"):
        LockedTonguePredictionReader(
            tongue_model=lambda: model, tongue_model_lock=lambda: lock,
        ).predict("capture.jpg")
    assert not lock.locked()
