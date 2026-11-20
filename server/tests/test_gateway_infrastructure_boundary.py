"""Infrastructure boundaries: domain services own model slots, admission is gateway-owned."""

import ast
import asyncio
import threading
from contextlib import contextmanager
from pathlib import Path

import pytest

from server.detection_errors import DetectionBusyError
from server.compute.speech.types import Transcript, TranscriptSegment

SERVER_ROOT = Path(__file__).resolve().parents[1]

CAPABILITY_DIRS = (
    "compute/speech/speech/adapters",
    "compute/tongue/tongue_recognition/adapters",
    "capture",
    "analysis/health",
    "analysis/emotion",
)

FORBIDDEN_TOKENS = ("use_runtime", "RuntimeContext", "analysis_session")
FORBIDDEN_MODULES = ("server.runtime", "server.execution")


class _VoiceModel:
    def __init__(self, lock):
        self._lock = lock
        self.calls = []

    def generate(self, **kwargs):
        assert self._lock.locked()
        self.calls.append(kwargs)
        return [{"text": "甲"}]


class _TongueModel:
    def __init__(self, lock):
        self._lock = lock
        self.paths = []

    def predict(self, image_path):
        assert self._lock.locked()
        self.paths.append(image_path)
        return {"color": {"class": "red", "probability": 0.9}}


def test_task_admission_bounds_concurrency_without_resources():
    from server.concurrency import TaskAdmission

    admission = TaskAdmission()
    with admission.acquire("emotion", "busy"):
        with pytest.raises(DetectionBusyError, match="busy"):
            with admission.acquire("fatigue", "busy"):
                pass

    # 释放后可再次进入
    with admission.acquire("fatigue", "busy"):
        pass


def test_speech_adapter_runs_with_explicit_providers_only(tmp_path):
    from server.compute.speech.service import SpeechService
    from server.compute.speech.types import AudioData

    scratch = tmp_path / "scratch"
    scratch.mkdir()
    adapter = SpeechService(lambda: None)
    adapter.model = _VoiceModel(adapter.model_lock)
    pcm = AudioData(b"\x00\x00" * 16000, 16000)

    result = adapter.result(pcm)

    assert result == Transcript((TranscriptSegment("甲"),))
    assert adapter.model.calls == [{
        "input": pcm.pcm, "input_format": "pcm_s16le", "fs": 16000,
        "language": "auto", "use_itn": True,
    }]
    assert list(scratch.iterdir()) == []


def test_locked_tongue_reader_runs_with_explicit_providers_only():
    from server.compute.tongue.service import LockedTonguePredictionReader

    lock = threading.Lock()
    model = _TongueModel(lock)
    reader = LockedTonguePredictionReader(
        tongue_model=lambda: model,
        tongue_model_lock=lambda: lock,
    )

    assert reader.predict("capture.jpg") == {"color": {"class": "red", "probability": 0.9}}
    assert model.paths == ["capture.jpg"]
    assert not lock.locked()

    missing = LockedTonguePredictionReader(
        tongue_model=lambda: None,
        tongue_model_lock=threading.Lock,
    )
    with pytest.raises(RuntimeError, match="TongueRecognitionService is not loaded"):
        missing.predict("capture.jpg")


def test_capability_modules_have_no_runtime_context_or_session_compatibility():
    for relative in CAPABILITY_DIRS:
        for path in sorted((SERVER_ROOT / relative).rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            source = path.read_text(encoding="utf-8")
            for token in FORBIDDEN_TOKENS:
                assert token not in source, f"{path}: {token}"
            for node in ast.walk(ast.parse(source, filename=str(path))):
                modules = []
                if isinstance(node, ast.Import):
                    modules = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    modules = [node.module]
                for module in modules:
                    assert not any(
                        module == forbidden or module.startswith(forbidden + ".")
                        for forbidden in FORBIDDEN_MODULES
                    ), f"{path}: {module}"


def test_execution_compatibility_package_is_removed():
    assert not (SERVER_ROOT / "execution").exists()
