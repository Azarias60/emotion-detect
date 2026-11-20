"""Expression recognition use case and classifier adapter contracts."""

from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from server.compute.expression.expression_recognition.adapters.mobilenet import MobileNetV2ExpressionClassifier
from server.compute.expression.expression_recognition.application import ExpressionRecognitionApplication
from server.compute.expression.types import ExpressionObservation, ExpressionObservations


class FakeClassifier:
    def __init__(self, predictions, events=None):
        self.predictions = predictions
        self.calls = []
        self.events = events

    def predict(self, face_images):
        self.calls.append(tuple(face_images))
        if self.events is not None:
            self.events.append("predict")
        return self.predictions


class FakeClock:
    def __init__(self, timestamp, events=None):
        self.timestamp = timestamp
        self.calls = 0
        self.events = events

    def now(self):
        self.calls += 1
        if self.events is not None:
            self.events.append("clock")
        return self.timestamp


def test_use_case_returns_owned_result_and_timestamps_after_inference():
    events = []
    classifier = FakeClassifier((
        ExpressionObservation("happy", 0.77777, None, 3),
        ExpressionObservation("neutral", 0.5, None, 4),
    ), events)
    clock = FakeClock(12.34567, events)
    result = ExpressionRecognitionApplication(classifier, clock).recognize(["face-a", "face-b"])
    assert result == ExpressionObservations(
        observations=(
            ExpressionObservation("happy", 0.77777, 12.34567, 3),
            ExpressionObservation("neutral", 0.5, 12.34567, 4),
        ),
        class_counts=(("happy", 1), ("neutral", 1)),
    )
    assert classifier.calls == [("face-a", "face-b")]
    assert clock.calls == 1
    assert events == ["predict", "clock"]
    with pytest.raises(FrozenInstanceError):
        result.observations[0].label = "sad"


def test_mobilenet_adapter_preserves_class_order_indices_and_scores():
    class Engine:
        def inference(self, face_images):
            assert face_images == ["face-a", "face-b"]
            return [3, 4], [0.123456, 0.987654]

    names = ["angry", "disgust", "fear", "happy", "neutral", "sad", "surprise"]
    result = MobileNetV2ExpressionClassifier(Engine(), names).predict(["face-a", "face-b"])
    assert result == (
        ExpressionObservation("happy", 0.123456, None, 3),
        ExpressionObservation("neutral", 0.987654, None, 4),
    )


def test_empty_face_input_does_not_invoke_classifier():
    class Engine:
        def inference(self, _):
            pytest.fail("must not infer without faces")

    adapter = MobileNetV2ExpressionClassifier(Engine(), ["angry"])
    assert adapter.predict(()) == ()


def test_inner_layers_have_no_outward_dependencies():
    import server.compute.expression.expression_recognition.application as application
    import server.compute.expression.expression_recognition.domain as domain
    import server.compute.expression.expression_recognition.ports as ports

    for module in (application, domain, ports):
        source = Path(module.__file__).read_text().lower()
        assert "torch" not in source
        assert "fastapi" not in source
        assert "server.analysis" not in source
