"""M7: inherited tongue predictor stays unchanged while ownership moves.

Weights are never loaded. Check error identity, the production assembly, the
public error mapping at the recognition adapter boundary, and the borrowed
four-model inference behavior with controlled in-memory logits.
"""

import ast
import sys
import threading
import types
from pathlib import Path

import pytest

from server.detection_errors import (
    DetectionFlowError,
    DetectionInputError,
)


def test_tongue_predictor_error_types_belong_only_to_recognition():
    from server.compute.tongue.tongue_recognition import errors
    for name in (
        "ModelLoadError", "ImageNotFoundError", "InvalidImageError", "InferenceError",
    ):
        assert issubclass(getattr(errors, name), errors.TonguePredictorError)


def test_mapped_reader_translates_predictor_failures_to_public_errors():
    from server.compute.tongue.service import (
        MappedTonguePredictionReader,
    )
    from server.compute.tongue.tongue_recognition.errors import (
        ImageNotFoundError,
        InferenceError,
        InvalidImageError,
        ModelLoadError,
    )

    def mapped(error=None):
        class Reader:
            def is_available(self):
                return True

            def predict(self, image_path):
                if error is not None:
                    raise error
                return {"color": {"class": "red", "probability": 0.9}}

        return MappedTonguePredictionReader(Reader())

    with pytest.raises(DetectionInputError, match="^无法解析上传的图片$") as caught:
        mapped(InvalidImageError("无法识别图片格式: /tmp/tongue/capture.jpg")).predict("x.jpg")
    assert isinstance(caught.value.__cause__, InvalidImageError)

    with pytest.raises(DetectionFlowError, match="^检测失败，请查看服务端日志$"):
        mapped(ModelLoadError("权重文件不存在: weights/tongue/tongue_color")).predict("x.jpg")

    for error in (InferenceError("模型 [color] 推理失败: CUDA oom"), ImageNotFoundError("/secret/path.jpg")):
        # reader 翻译为域家族；公开文案由基类收敛，不外泄 predictor 细节。
        with pytest.raises(DetectionFlowError) as caught:
            mapped(error).predict("x.jpg")
        assert str(caught.value) == "检测失败，请查看服务端日志"
        assert "CUDA oom" not in str(caught.value)
        assert "/secret/path.jpg" not in str(caught.value)

    assert mapped().predict("capture.jpg") == {"color": {"class": "red", "probability": 0.9}}


def test_mapped_reader_rejects_use_before_model_load():
    from server.compute.tongue.service import (
        LockedTonguePredictionReader,
        MappedTonguePredictionReader,
    )

    mapped = MappedTonguePredictionReader(LockedTonguePredictionReader(
        tongue_model=lambda: None, tongue_model_lock=threading.Lock,
    ))
    with pytest.raises(RuntimeError, match="TongueRecognitionService is not loaded"):
        mapped.predict("capture.jpg")


def test_mapped_reader_wraps_locked_reader_and_holds_model_lock():
    from server.compute.tongue.service import (
        LockedTonguePredictionReader,
        MappedTonguePredictionReader,
    )

    lock = threading.Lock()
    calls = []

    class Model:
        def predict(self, path):
            assert lock.locked()
            calls.append(path)
            return {"color": {"class": "red", "probability": 0.9}}

    mapped = MappedTonguePredictionReader(LockedTonguePredictionReader(
        tongue_model=lambda: Model(), tongue_model_lock=lambda: lock,
    ))

    assert mapped.predict("capture.jpg") == {"color": {"class": "red", "probability": 0.9}}
    assert calls == ["capture.jpg"]
    assert not lock.locked()


def test_prediction_assembly_instantiates_new_owner_and_preserves_device(monkeypatch):
    from server.compute.tongue import assembly as registry

    requests = []

    class FakePredictor:
        def __init__(self, device=None):
            requests.append(device)

    fake_module = types.ModuleType("server.compute.tongue.tongue_recognition.adapters.efficientnet")
    fake_module.TonguePredictor = FakePredictor
    monkeypatch.setitem(
        sys.modules, "server.compute.tongue.tongue_recognition.adapters.efficientnet", fake_module,
    )

    assert isinstance(registry.create_tongue_predictor(), FakePredictor)
    assert requests == [None]


def test_model_adapter_has_no_implementation_imports_from_health():
    adapter_path = Path(__file__).resolve().parents[1] / (
        "compute/tongue/tongue_recognition/adapters/efficientnet.py"
    )
    parsed = ast.parse(adapter_path.read_text(encoding="utf-8"))
    imports = [
        n.module for n in ast.walk(parsed)
        if isinstance(n, ast.ImportFrom) and n.module
    ]
    assert "server.compute.tongue.tongue_recognition.errors" in imports
    assert not any(
        n.startswith((
            "server.api", "server.emotion_detection",
            "server.fatigue_assessment", "server.health_consultation",
        ))
        for n in imports
    )

    configs = [
        node for node in ast.walk(parsed)
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "_MODEL_CONFIGS"
            for target in node.targets
        )
    ]
    assert ast.literal_eval(configs[0].value) == [
        ("crack", "tongue_crack", 2),
        ("coated", "tongue_coated", 3),
        ("color", "tongue_color", 3),
        ("indentation", "tongue_indentation", 2),
    ]


@pytest.mark.model
def test_four_model_prediction_preprocessing_and_existing_result_values(tmp_path):
    pytest.importorskip("torch")
    import numpy as np
    import torch
    from PIL import Image
    from torchvision import transforms

    from server.compute.tongue.tongue_recognition.adapters.efficientnet import TonguePredictor

    class Network:
        def __init__(self, logits, name):
            self.logits = logits
            self.name = name

        def __call__(self, tensor):
            assert tensor.shape == (1, 3, 224, 224)
            assert tensor.min().item() >= 0.0 and tensor.max().item() <= 1.0
            calls.append(self.name)
            return torch.tensor([self.logits], dtype=torch.float32)

    calls = []
    logits = {
        "crack": [0.0, 3.0],
        "coated": [-2.0, 2.0, 0.0],
        "color": [0.0, 1.0, 3.0],
        "indentation": [3.0, 0.0],
    }
    labels = {
        "crack": {"0": "crack", "1": "normal"},
        "coated": {"0": "yellow", "1": "white", "2": "nocoated"},
        "color": {"0": "red", "1": "white", "2": "crimson"},
        "indentation": {"0": "normal", "1": "indentation"},
    }

    predictor = object.__new__(TonguePredictor)
    predictor._device = torch.device("cpu")
    predictor._transform = transforms.Compose([
        transforms.Resize(224), transforms.CenterCrop(224), transforms.ToTensor(),
    ])
    predictor.models = {name: Network(value, name) for name, value in logits.items()}
    predictor.class_indices = labels

    file = tmp_path / "tongue.png"
    Image.fromarray(np.full((300, 400, 3), 127, dtype=np.uint8)).save(file)

    result = predictor.predict(str(file))
    assert calls == ["crack", "coated", "color", "indentation"]
    assert {name: item["class"] for name, item in result.items()} == {
        "crack": "normal", "coated": "white", "color": "crimson",
        "indentation": "normal",
    }
    for name, item in result.items():
        probabilities = torch.softmax(torch.tensor(logits[name]), dim=0)
        assert item["probability"] == round(probabilities.max().item(), 4)


@pytest.mark.model
def test_original_missing_image_error_is_preserved(tmp_path):
    pytest.importorskip("torch")
    from server.compute.tongue.tongue_recognition.errors import ImageNotFoundError
    from server.compute.tongue.tongue_recognition.adapters.efficientnet import TonguePredictor

    # Do not load weights; the image-presence check runs before preprocessing.
    predictor = object.__new__(TonguePredictor)
    with pytest.raises(ImageNotFoundError, match="图片文件不存在"):
        predictor.predict(str(tmp_path / "missing.png"))


@pytest.mark.parametrize("stage", ["model", "context"])
@pytest.mark.model
def test_tongue_inference_failure_keeps_explicit_cause(stage, monkeypatch, tmp_path):
    pytest.importorskip("torch")
    from contextlib import contextmanager
    from PIL import Image
    import torch
    from server.compute.tongue.tongue_recognition.adapters.efficientnet import TonguePredictor
    from server.compute.tongue.tongue_recognition.errors import InferenceError
    failure = RuntimeError("inference failed")

    class Model:
        def __call__(self, value):
            raise failure

    predictor = object.__new__(TonguePredictor)
    predictor._device = torch.device("cpu")
    predictor._transform = lambda _: torch.zeros(3, 8, 8)
    predictor.models = {"crack": Model()}
    predictor.class_indices = {}
    if stage == "context":
        @contextmanager
        def broken_context():
            raise failure
            yield
        monkeypatch.setattr(torch, "no_grad", broken_context)
    image = tmp_path / "tongue.png"
    Image.new("RGB", (8, 8)).save(image)
    with pytest.raises(InferenceError) as caught:
        predictor.predict(image)
    assert caught.value.__cause__ is failure
    if stage == "model":
        assert "crack" in str(caught.value)
