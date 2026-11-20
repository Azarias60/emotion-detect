"""Boundary between timestamped images, landmark extraction and behavior."""

import ast
from contextlib import closing
from pathlib import Path
from unittest.mock import Mock

import numpy as np
import pytest

from server.compute.fatigue.behavior_recognition import engine
from server.compute.fatigue.behavior_recognition.adapters.video import VideoBehaviorRecognizer
from server.compute.fatigue.types import FacialLandmarkExtractionResult

pytestmark = pytest.mark.real_behavior_adapter


def test_behavior_implementation_imports_landmark_result_only():
    from server.compute.fatigue import service

    for module in (engine, service):
        tree = ast.parse(Path(module.__file__).read_text())
        imported = [
            item.module for item in ast.walk(tree)
            if isinstance(item, ast.ImportFrom) and item.module
        ]
        assert "server.compute.fatigue.facial_landmarks.adapters.dlib_68" not in imported
        assert "server.compute.fatigue.facial_landmarks.service" not in imported
        assert not any(name.startswith("server.coordination") for name in imported)
    assert "server.compute.fatigue.types" in [
        item.module for item in ast.walk(ast.parse(Path(engine.__file__).read_text()))
        if isinstance(item, ast.ImportFrom)
    ]


def test_engine_accepts_result_without_executing_another_capability():
    analyzer = engine.FatigueAnalyzer()
    analyzer.process_frame(FacialLandmarkExtractionResult((), "no_face"), 17.3)
    assert analyzer.behavior_result() is None


def test_video_runner_streams_frames_with_original_timestamps_and_orientation(monkeypatch):
    seen = []
    process_frame = engine.FatigueAnalyzer.process_frame

    def record(self, result, timestamp):
        seen.append(timestamp)
        process_frame(self, result, timestamp)

    def frames():
        yield 14.0, np.zeros((16, 32, 3), dtype=np.uint8)
        assert seen == [14.0]
        yield 14.07, np.zeros((32, 16, 3), dtype=np.uint8)
        yield 14.2, np.zeros((16, 32, 3), dtype=np.uint8)

    monkeypatch.setattr(engine.FatigueAnalyzer, "process_frame", record)
    reader = Mock()
    reader.extract.return_value = FacialLandmarkExtractionResult((), "no_face")
    recognizer = VideoBehaviorRecognizer(reader)
    assert recognizer.observe_frames(frames()) is None
    assert seen == [14.0, 14.07, 14.2]
    assert [call.args[0].shape for call in reader.extract.call_args_list] == [
        (480, 640, 3), (640, 480, 3), (480, 640, 3),
    ]
    recognizer.close()
    reader.close.assert_called_once_with()


def test_landmark_reader_failure_preserves_exception_and_stream_cleanup():
    failure = FileNotFoundError("landmark resource missing")
    reader = Mock()
    reader.extract.side_effect = failure
    finalized = []

    def frames():
        try:
            yield 9.0, np.zeros((16, 32, 3), dtype=np.uint8)
        finally:
            finalized.append(True)

    with closing(frames()) as stream, pytest.raises(FileNotFoundError) as caught:
        VideoBehaviorRecognizer(reader).observe_frames(stream)
    assert caught.value is failure
    assert finalized == [True]


def test_empty_frame_stream_never_calls_landmark_reader():
    reader = Mock()
    assert VideoBehaviorRecognizer(reader).observe_frames(iter(())) is None
    reader.extract.assert_not_called()
