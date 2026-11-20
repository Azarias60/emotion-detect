"""Independent behavior-observation payload; no cv2/dlib/model import necessary."""

import ast
from dataclasses import asdict, FrozenInstanceError
from pathlib import Path

import pytest

from server.compute.fatigue.types import BehaviorObservation
from server.compute.fatigue.behavior_recognition.domain import summarize_behavior
from server.compute.fatigue.fatigue_scoring.domain import assess_fatigue


def test_behavior_output_does_not_contain_fatigue_scoring_or_advice():
    result = summarize_behavior(
        blink_count=3, yawn_count=2, nod_count=1,
        closed_seconds=6.003, speaking_seconds=3.75,
        face_seconds=30, video_seconds=60,
    )
    assert isinstance(result, BehaviorObservation)
    assert asdict(result) == pytest.approx({
        "blink_count": 3, "blink_frequency": 6.0, "yawn_count": 2, "nod_count": 1,
        "nod_frequency": 2.0, "perclos_percent": 20.01, "speaking_percent": 12.5,
        "face_coverage": 0.5,
    })
    assert not hasattr(result, "fatigue_score")
    assert not hasattr(result, "valid")
    assert not hasattr(result, "status")
    with pytest.raises(FrozenInstanceError):
        result.blink_count = 123


def test_behavior_rate_for_assessment_is_not_presentation_rounded():
    # 5.0004 blinks/min rounds to 5.0 for display but is above the <= 5 rule.
    result = summarize_behavior(
        blink_count=1, yawn_count=0, nod_count=0,
        closed_seconds=0.0, speaking_seconds=0.0,
        face_seconds=11.999, video_seconds=59.995,
    )
    assert round(result.blink_frequency, 2) == 5.0
    assessment = assess_fatigue(
        perclos_percent=result.perclos_percent,
        blink_frequency=result.blink_frequency,
        yawn_count=result.yawn_count,
        nod_frequency=result.nod_frequency,
    )
    assert assessment.score == 0
    assert result.face_coverage == 0.2


def test_behavior_domain_has_no_model_or_fatigue_dependencies():
    source = Path(__import__("server.compute.fatigue.behavior_recognition.domain", fromlist=["x"]).__file__).read_text()
    imports = [
        node.module
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.ImportFrom)
    ]
    assert imports == ["server.compute.fatigue.types"]
    from server.compute.fatigue.types import behavior
    contract_imports = [node.module for node in ast.walk(ast.parse(Path(behavior.__file__).read_text()))
                        if isinstance(node, ast.ImportFrom)]
    assert contract_imports == ["dataclasses"]


def test_empty_and_no_face_samples_are_gated_outside_the_domain():
    # The payload itself stays a pure projection; an absent face sample is
    # expressed as None at the observation boundary, not as a status here.
    empty = summarize_behavior(
        blink_count=0, yawn_count=0, nod_count=0,
        closed_seconds=0.0, speaking_seconds=0.0, face_seconds=0, video_seconds=0,
    )
    no_face = summarize_behavior(
        blink_count=0, yawn_count=0, nod_count=0,
        closed_seconds=0.0, speaking_seconds=0.0, face_seconds=0, video_seconds=4,
    )
    assert empty.face_coverage == 0.0
    assert no_face.face_coverage == 0.0
