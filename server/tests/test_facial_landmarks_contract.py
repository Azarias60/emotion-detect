"""Contract tests for dlib-68 facial landmark extraction without real weights."""

import sys
from types import ModuleType, SimpleNamespace

import numpy as np
import pytest

from server.compute.fatigue.behavior_recognition.engine import FatigueAnalyzer
from server.compute.fatigue.facial_landmarks.adapters import dlib_68 as dlib_module
from server.compute.fatigue.facial_landmarks.adapters.dlib_68 import Dlib68LandmarkReader
from server.compute.fatigue.facial_landmarks.service import FacialLandmarkExtraction
from server.compute.fatigue.types import FacialLandmarkExtractionResult


def _install_dlib(monkeypatch, rectangles, raw_points):
    predictor_calls = []
    detector = lambda image, upsample: rectangles

    def predictor(image, rectangle):
        predictor_calls.append(rectangle)
        return SimpleNamespace(parts=lambda: raw_points)

    dlib = ModuleType("dlib")
    dlib.get_frontal_face_detector = lambda: detector
    dlib.shape_predictor = lambda path: predictor
    monkeypatch.setitem(sys.modules, "dlib", dlib)
    return predictor_calls


class _NoFaceReader:
    def __init__(self):
        self.calls = []

    def extract(self, image):
        self.calls.append(image)
        return FacialLandmarkExtractionResult(
            faces=(),
            status="no_face",
            model_source="dlib_68",
            quality="insufficient",
        )



def test_dlib_model_missing_fails_loading(tmp_path):
    missing = tmp_path / "missing.dat"
    with pytest.raises(FileNotFoundError, match="missing.dat"):
        Dlib68LandmarkReader(missing)


def test_dlib_preserves_pixel_coordinates_and_first_face_policy(monkeypatch, tmp_path):
    model = tmp_path / "shape_predictor_68_face_landmarks.dat"
    model.write_bytes(b"fake model")
    raw_points = [SimpleNamespace(x=index + 1, y=index + 2) for index in range(68)]
    predictor_calls = _install_dlib(
        monkeypatch, rectangles=["first-face", "second-face"], raw_points=raw_points
    )
    reader = Dlib68LandmarkReader(model)
    frame = np.zeros((48, 64, 3), dtype=np.uint8)

    result = reader.extract(frame)

    assert result.status == "ok"
    assert result.model_source == "dlib_68"
    assert result.quality == "not_reported"
    face = result.faces[0]
    assert face.coordinate_space == "pixel"
    assert (face.image_width, face.image_height) == (64, 48)
    assert len(face.points) == 68
    assert (face.points[0].x, face.points[0].y) == (1.0, 2.0)
    assert predictor_calls == ["first-face"]


@pytest.mark.parametrize(
    "rectangles, raw_points, expected_status, expected_reason",
    [
        ([], [], "no_face", None),
        (["face"], [], "invalid_landmarks", "empty_landmarks"),
        (["face"], [SimpleNamespace(x=i, y=i) for i in range(67)],
         "invalid_landmarks", "invalid_landmarks"),
    ],
)
def test_dlib_distinguishes_no_face_and_invalid_landmarks(
    monkeypatch, tmp_path, rectangles, raw_points, expected_status, expected_reason
):
    model = tmp_path / "shape_predictor.dat"
    model.write_bytes(b"fake model")
    _install_dlib(monkeypatch, rectangles=rectangles, raw_points=raw_points)
    reader = Dlib68LandmarkReader(model)

    result = reader.extract(np.zeros((48, 64, 3), dtype=np.uint8))

    assert result.status == expected_status
    assert result.reason == expected_reason



def test_fatigue_consumer_rejects_face_mesh_points_instead_of_treating_them_as_dlib():
    class FixedExtraction:
        def execute(self, image):
            return SimpleNamespace(
                faces=(SimpleNamespace(points=tuple(range(478))),),
                status="ok",
                quality="not_reported",
                model_source="face_landmarker",
            )

    analyzer = FatigueAnalyzer()
    landmarks = FixedExtraction().execute(np.zeros((48, 64, 3), dtype=np.uint8))
    with pytest.raises(ValueError, match="需要 dlib 68 点，收到 face_landmarker"):
        analyzer.process_frame(landmarks, 0.0)


def test_invalid_image_is_distinct_from_missing_model(monkeypatch, tmp_path):
    model = tmp_path / "shape_predictor.dat"
    model.write_bytes(b"fake model")
    _install_dlib(monkeypatch, rectangles=[], raw_points=[])
    reader = Dlib68LandmarkReader(model)
    result = reader.extract(np.zeros((0, 0, 3), dtype=np.uint8))

    assert result.status == "invalid_image"
    assert result.reason == "invalid_image"
    assert result.quality == "insufficient"


def test_missing_landmark_tools_fail_until_model_is_installed(monkeypatch, tmp_path):
    model = tmp_path / "shape_predictor.dat"
    with pytest.raises(FileNotFoundError):
        Dlib68LandmarkReader(model)
    model.write_bytes(b"fake model")
    _install_dlib(monkeypatch, rectangles=[], raw_points=[])
    loaded = Dlib68LandmarkReader(model)
    assert loaded.extract(np.zeros((48, 64, 3), dtype=np.uint8)).status == "no_face"


def test_no_face_frames_do_not_become_normal_or_zero_measurements():
    extractor = FacialLandmarkExtraction(_NoFaceReader()).execute
    analyzer = FatigueAnalyzer()
    for timestamp in (7.0, 7.05, 7.11, 7.14, 7.19):
        result = extractor(np.zeros((480, 640, 3), dtype=np.uint8))
        analyzer.process_frame(result, timestamp)
    result = analyzer.behavior_result()
    assert result is None  # No usable face frames yield no observation.


@pytest.mark.real_behavior_adapter
def test_fatigue_frame_pass_propagates_inference_failure(monkeypatch):
    from server.compute.fatigue.behavior_recognition.adapters import video as fatigue_video

    def fail(*args, **kwargs):
        raise RuntimeError("damaged model")

    class Reader:
        is_available = True

        def extract(self, *args, **kwargs):
            return fail()

    with pytest.raises(RuntimeError, match="damaged model"):
        fatigue_video.VideoBehaviorRecognizer(Reader()).observe_frames((_frame(),))


@pytest.mark.real_behavior_adapter
def test_fatigue_container_loop_releases_the_capture_even_when_the_core_fails(monkeypatch):
    """容器基线（测试侧）：核心失败也必须释放底层 capture。"""
    from server.compute.fatigue.behavior_recognition.adapters import video as fatigue_video

    releases = []

    class Capture:
        def isOpened(self):
            return True

        def read(self):
            return True, np.zeros((16, 32, 3), dtype=np.uint8)

        def release(self):
            releases.append(True)

    def fail(*args, **kwargs):
        raise RuntimeError("damaged model")

    class Reader:
        is_available = True

        def extract(self, *args, **kwargs):
            return fail()

    capture = Capture()

    def iterate():
        while True:
            ok, frame = capture.read()
            if not ok:
                return
            yield 8.0, frame

    with pytest.raises(RuntimeError, match="damaged model"):
        try:
            fatigue_video.VideoBehaviorRecognizer(Reader()).observe_frames(iterate())
        finally:
            capture.release()
    assert releases == [True]


def test_dlib_readers_do_not_share_models_between_instances(monkeypatch):
    loads = []

    def load(path):
        def detect(image, upsample):
            return []

        tools = detect, object()
        loads.append(tools)
        return tools

    monkeypatch.setattr(dlib_module, "_load_landmark_tools", load)
    first = Dlib68LandmarkReader("same-model")
    second = Dlib68LandmarkReader("same-model")
    assert len(loads) == 2 and loads[0] is not loads[1]
    frame = np.zeros((48, 64, 3), dtype=np.uint8)
    first.close()
    with pytest.raises(RuntimeError, match="Dlib68LandmarkReader is closed"):
        first.extract(frame)
    assert second.extract(frame).status == "no_face"
    second.close()
    with pytest.raises(RuntimeError, match="Dlib68LandmarkReader is closed"):
        second.extract(frame)


def _frame():
    return 8.0, np.zeros((16, 32, 3), dtype=np.uint8)
