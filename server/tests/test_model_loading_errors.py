"""Missing model dependencies and weights must fail loading without translation."""

import builtins
import sys
from types import SimpleNamespace

import pytest

from server.compute.speech.speech.adapters.funasr import load_voice_model
from server.compute.tongue.assembly import create_tongue_predictor
from server.compute.expression.assembly import create_emotion_predictor


@pytest.mark.parametrize("failure_type", [FileNotFoundError, RuntimeError])
@pytest.mark.parametrize("kind", [
    "voice",
    pytest.param("face", marks=pytest.mark.model),
    pytest.param("expression", marks=pytest.mark.model),
])
def test_model_loading_preserves_original_failure(monkeypatch, failure_type, kind):
    failure = failure_type("load defect")
    def fail(*args, **kwargs):
        raise failure

    if kind == "voice":
        monkeypatch.setitem(sys.modules, "funasr", SimpleNamespace(AutoModel=fail))
        load = lambda: load_voice_model("cpu")
    elif kind == "face":
        from server.compute.expression.face_detection.adapters.rfb_ssd import RFBSSDAdapter
        from server.vendor.light_detector import detector

        monkeypatch.setattr(detector, "Detector", fail)
        load = RFBSSDAdapter
    else:
        from server.compute.expression.expression_recognition.adapters.mobilenet import MobileNetV2ExpressionClassifier
        from server.vendor.classifier import inference

        monkeypatch.setattr(inference, "Inference", fail)
        load = lambda: MobileNetV2ExpressionClassifier.create(SimpleNamespace(class_name=["happy"]))

    with pytest.raises(failure_type) as caught:
        load()
    assert caught.value is failure


@pytest.mark.parametrize("loader,dependency", [
    (lambda: load_voice_model("cpu"), "funasr"),
    (create_emotion_predictor, "torch"),
    (create_tongue_predictor, "server.compute.tongue.tongue_recognition.adapters.efficientnet"),
])
def test_missing_model_dependency_propagates_native_exception(monkeypatch, loader, dependency):
    original_import = builtins.__import__
    failure = ModuleNotFoundError("dependency missing", name=dependency)
    def missing(name, *args, **kwargs):
        if name == dependency:
            raise failure
        return original_import(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", missing)
    with pytest.raises(ModuleNotFoundError) as caught:
        loader()
    assert caught.value is failure


@pytest.mark.parametrize("stage", ["build", "device", "deserialize", "state_dict", "eval"])
@pytest.mark.model
def test_tongue_loader_classifies_only_weight_loading_runtime_errors(monkeypatch, tmp_path, stage):
    from server.compute.tongue.tongue_recognition.adapters import efficientnet
    from server.compute.tongue.tongue_recognition.errors import ModelLoadError

    directory = tmp_path / "tongue" / "tongue_crack"
    directory.mkdir(parents=True)
    (directory / "class_indices.json").write_text('{"0":"normal","1":"crack"}', encoding="utf-8")
    predictor = object.__new__(efficientnet.TonguePredictor)
    predictor._MODEL_CONFIGS = [("crack", "tongue_crack", 2)]
    predictor._base_dir = tmp_path
    predictor._device = "cpu"
    predictor.models = {}
    predictor.class_indices = {}
    failure = RuntimeError("internal load defect")
    def step(name, result=None):
        def execute(*args, **kwargs):
            if stage == name:
                raise failure
            return result
        return execute
    model = SimpleNamespace(to=step("device"), load_state_dict=step("state_dict"), eval=step("eval"))
    monkeypatch.setattr(efficientnet, "efficientnet_b0", step("build", model))
    monkeypatch.setattr(efficientnet.torch, "load", step("deserialize", {}))
    known = stage in {"deserialize", "state_dict"}
    with pytest.raises(ModelLoadError if known else RuntimeError) as caught:
        predictor._load_all_models()
    if known:
        assert caught.value.__cause__ is failure
        assert "crack" in str(caught.value)
    else:
        assert caught.value is failure


@pytest.mark.model
def test_tongue_missing_mapping_preserves_file_error_cause(tmp_path, monkeypatch):
    from server import settings
    from server.compute.tongue.tongue_recognition.adapters.efficientnet import TonguePredictor
    from server.compute.tongue.tongue_recognition.errors import ModelLoadError

    monkeypatch.setattr(settings, "TONGUE_WEIGHTS_DIR", tmp_path)
    with pytest.raises(ModelLoadError, match="4/4") as caught:
        TonguePredictor(device="cpu")
    assert isinstance(caught.value.__cause__, FileNotFoundError)


@pytest.mark.parametrize("failure_type", [FileNotFoundError, ImportError, RuntimeError, ValueError, OSError])
def test_fatigue_service_loading_preserves_all_failures(monkeypatch, failure_type):
    from server.compute.fatigue.assembly import create_fatigue_observation_service
    from server.compute.fatigue.facial_landmarks.adapters import dlib_68

    failure = failure_type("loader failure")

    def fail(path):
        raise failure

    monkeypatch.setattr(dlib_68, "_load_landmark_tools", fail)
    with pytest.raises(failure_type) as caught:
        create_fatigue_observation_service().load()
    assert caught.value is failure
