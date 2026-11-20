"""Face detection Result contracts and consumer integration without model weights."""

from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace

import numpy as np
import pytest

from server.compute.expression.face_detection.adapters.rfb_ssd import RFBSSDAdapter
from server.compute.expression.types import FaceDetection, FaceDetectionResult


class _Detector:
    def __init__(self, boxes, labels, probabilities):
        self.boxes = boxes
        self.labels = labels
        self.probabilities = probabilities
        self.calls = []

    def detect_image(self, image, vis=False):
        self.calls.append((image, vis))
        return self.boxes, self.labels, self.probabilities


def test_adapter_preserves_detection_order_coordinates_scores_and_labels():
    detector = _Detector(
        np.asarray([[1, 2, 30, 40], [5, 6, 50, 60]], dtype=np.float32),
        np.asarray([1, 1]),
        np.asarray([0.91, 0.86], dtype=np.float32),
    )
    result = RFBSSDAdapter(detector=detector).detect("rgb-image")
    assert result == FaceDetectionResult((
        FaceDetection(1.0, 2.0, 30.0, 40.0, float(np.float32(0.91)), 1),
        FaceDetection(5.0, 6.0, 50.0, 60.0, float(np.float32(0.86)), 1),
    ), "ok")
    assert detector.calls == [("rgb-image", False)]


def test_empty_detection_is_an_explicit_no_face_result():
    detector = _Detector(np.asarray([]), np.asarray([]), np.asarray([]))
    result = RFBSSDAdapter(detector=detector).detect("rgb-image")
    assert result == FaceDetectionResult((), "no_face")
    assert detector.calls == [("rgb-image", False)]


def test_missing_image_does_not_invoke_detector():
    detector = _Detector(np.asarray([]), np.asarray([]), np.asarray([]))
    result = RFBSSDAdapter(detector=detector).detect(None)
    assert result == FaceDetectionResult((), "missing_image")
    assert detector.calls == []


def test_model_failure_is_not_misreported_as_no_face():
    class BrokenDetector:
        def detect_image(self, image, vis=False):
            raise RuntimeError("weights unavailable")

    with pytest.raises(RuntimeError, match="weights unavailable"):
        RFBSSDAdapter(detector=BrokenDetector()).detect("rgb-image")


def test_malformed_model_output_is_rejected_instead_of_becoming_no_face():
    detector = _Detector(
        np.asarray([[1, 2, 30, 40]], dtype=np.float32),
        np.asarray([1]),
        np.asarray([]),
    )
    with pytest.raises(ValueError, match="same length"):
        RFBSSDAdapter(detector=detector).detect("rgb-image")


def test_video_expression_coordinator_crops_from_typed_face_detection_result(monkeypatch):
    from server.compute.expression.expression_recognition.adapters.video import VideoExpressionCoordinator

    detection = FaceDetection(10.0, 20.0, 30.0, 40.0, 0.95, 1)
    reader = SimpleNamespace(
        detect=lambda image: FaceDetectionResult((detection,), "ok")
    )
    expression_result = object()

    class ExpressionRecognizer:
        def __init__(self):
            self.inputs = []

        def recognize(self, faces):
            self.inputs.append(tuple(faces))
            return expression_result

    recognizer = ExpressionRecognizer()
    predictor = VideoExpressionCoordinator(
        face_detection=reader, expression_recognition=recognizer
    )
    observed = {}

    def extend_xyxy(boxes, scale):
        observed["boxes_before_crop"] = boxes.copy()
        observed["scale"] = scale
        return boxes

    def get_bboxes_crop(image, boxes):
        observed["image"] = image
        observed["boxes"] = boxes.copy()
        return ["cropped-face"]

    pybaseutils = ModuleType("pybaseutils")
    pybaseutils.coords_utils = SimpleNamespace(extend_xyxy=extend_xyxy)
    pybaseutils.image_utils = SimpleNamespace(get_bboxes_crop=get_bboxes_crop)
    monkeypatch.setitem(sys.modules, "pybaseutils", pybaseutils)

    image = object()
    assert predictor.task(image) is expression_result
    np.testing.assert_array_equal(
        observed["boxes_before_crop"], np.asarray([[10, 20, 30, 40]], dtype=np.float32)
    )
    assert observed["scale"] == [1.1, 1.1]
    np.testing.assert_array_equal(
        observed["boxes"], np.asarray([[10, 20, 30, 40]], dtype=np.float32)
    )
    assert observed["image"] is image
    assert recognizer.inputs == [("cropped-face",)]


@pytest.mark.parametrize(
    ("image", "result"),
    [
        ("rgb-image", FaceDetectionResult((), "no_face")),
        (None, FaceDetectionResult((), "missing_image")),
    ],
)
def test_video_expression_coordinator_skips_crop_without_detections(monkeypatch, image, result):
    from server.compute.expression.expression_recognition.adapters.video import VideoExpressionCoordinator

    class Reader:
        def detect(self, _image):
            # 真实适配器对 None 返回 missing_image；这里按参数等价复刻其出口。
            return result

    class ExpressionRecognizer:
        def __init__(self):
            self.inputs = []

        def recognize(self, faces):
            self.inputs.append(tuple(faces))
            return "empty-expression-result"

    recognizer = ExpressionRecognizer()
    predictor = VideoExpressionCoordinator(
        face_detection=Reader(), expression_recognition=recognizer,
    )
    pybaseutils = ModuleType("pybaseutils")
    monkeypatch.setitem(sys.modules, "pybaseutils", pybaseutils)

    assert predictor.task(image) == "empty-expression-result"
    assert recognizer.inputs == [()]


def test_domain_layers_do_not_depend_on_numpy_or_model_frameworks():
    import server.compute.expression.service as entry
    import server.compute.expression.types as domain

    for module in (entry, domain):
        source = Path(module.__file__).read_text().lower()
        assert "numpy" not in source
        assert "torch" not in source
        assert "fastapi" not in source
