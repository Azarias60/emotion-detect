"""dlib-68 facial landmark result and application contracts without model weights."""

from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from server.compute.fatigue.facial_landmarks.service import FacialLandmarkExtraction
from server.compute.fatigue.types import (
    FaceLandmarks,
    FacialLandmarkExtractionResult,
    LandmarkPoint,
)


class _Reader:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def extract(self, image):
        self.calls.append(image)
        return self.result


def _face_result(face):
    return FacialLandmarkExtractionResult(
        faces=(face,) if face is not None else (),
        status="ok" if face is not None else "no_face",
        model_source="dlib_68" if face is not None else None,
        quality="not_reported" if face is not None else "insufficient",
    )


def test_use_case_reports_missing_image_without_calling_port():
    reader = _Reader(_face_result(FaceLandmarks((LandmarkPoint(25, 75),))))
    result = FacialLandmarkExtraction(reader).execute(None)
    assert result == FacialLandmarkExtractionResult(
        (), "missing_image"
    )
    assert reader.calls == []


def test_use_case_distinguishes_no_face_from_success():
    empty = FacialLandmarkExtraction(_Reader(_face_result(None))).execute(object())
    assert empty.status == "no_face"
    face = FaceLandmarks((LandmarkPoint(25, 75),))
    found = FacialLandmarkExtraction(
        _Reader(_face_result(face))
    ).execute(object())
    assert found.status == "ok"
    assert found.faces == (face,)


def test_use_case_rejects_readers_that_violate_the_port_signature():
    """Readers with a different input signature fail at the port boundary."""
    class IncompatibleReader:
        def extract(self, image, required_extra_input):
            return (FaceLandmarks((LandmarkPoint(25, 75),)),)

    with pytest.raises(TypeError):
        FacialLandmarkExtraction(IncompatibleReader()).execute(object())


def test_dlib_domain_rejects_face_mesh_normalized_coordinates():
    with pytest.raises(ValueError, match="face_mesh_landmarks"):
        FaceLandmarks((LandmarkPoint(25, 75),), coordinate_space="normalized")  # type: ignore[arg-type]


def test_dlib_domain_rejects_foreign_model_source():
    with pytest.raises(ValueError, match="face_mesh_landmarks"):
        FacialLandmarkExtractionResult(
            faces=(FaceLandmarks((LandmarkPoint(25, 75),)),),
            status="ok",
            model_source="face_landmarker",  # type: ignore[arg-type]
            quality="not_reported",
        )


def test_result_is_immutable_and_status_must_match_faces():
    result = FacialLandmarkExtractionResult((), "no_face")
    with pytest.raises(FrozenInstanceError):
        result.status = "ok"
    with pytest.raises(ValueError):
        FacialLandmarkExtractionResult((), "ok")
    with pytest.raises(ValueError):
        FacialLandmarkExtractionResult((FaceLandmarks(()),), "no_face")


def test_inner_layers_have_no_framework_or_runtime_imports():
    import server.compute.fatigue.facial_landmarks.service as application
    import server.compute.fatigue.types as domain
    import server.compute.fatigue.facial_landmarks.ports as ports

    for module in (application, domain, ports):
        source = Path(module.__file__).read_text().lower()
        assert "mediapipe" not in source
        assert "cv2" not in source
        assert "server.analysis" not in source
