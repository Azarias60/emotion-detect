"""Audio ingress and ASR ownership tests without model files; input is PCM bytes only."""
import wave
from pathlib import Path

import pytest

from server.compute.speech.service import SpeechService
from server.compute.speech.types import AudioData, Transcript, TranscriptSegment


class Model:
    def __init__(self, lock, text="<|zh|>甲。乙。丙"):
        self.lock = lock
        self.text = text
        self.calls = []

    def generate(self, **kwargs):
        assert self.lock.locked(), "FunASR model calls must hold the shared domain lock"
        self.calls.append(kwargs)
        return [{"text": self.text}]


def speech(model=True):
    adapter = SpeechService(lambda: None)
    adapter.model = Model(adapter.model_lock) if model else None
    return adapter


def _write_wav(path: Path, pcm: bytes = b"\x01\x00" * 16000) -> None:
    with wave.open(str(path), "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(16000)
        writer.writeframes(pcm)


def test_unloaded_model_is_internal_error(tmp_path):
    pcm = AudioData(pcm=b"\x00\x00" * 16, sample_rate=16000)
    with pytest.raises(RuntimeError, match="SpeechService is not loaded"):
        speech(model=False).result(pcm)


def test_pcm_is_transcribed_without_files(tmp_path):
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    adapter = speech()
    pcm = AudioData(pcm=b"\x01\x00" * 16000, sample_rate=16000)

    result = adapter.result(pcm)

    assert result == Transcript((TranscriptSegment("甲。 乙。"), TranscriptSegment("丙")))
    assert adapter.model.calls == [{
        "input": pcm.pcm, "input_format": "pcm_s16le", "fs": 16000,
        "language": "auto", "use_itn": True,
    }]
    assert list(scratch.iterdir()) == []


def test_missing_audio_skips_transcription(tmp_path):
    adapter = speech()
    assert adapter.result(None) is None
    assert adapter.result(AudioData(pcm=b"", sample_rate=16000)) is None
    assert adapter.model.calls == []


def test_gateway_speech_port_is_the_domain_owned_service():
    from server.tests.runtime_fixture import assembly

    assert isinstance(assembly.speech, SpeechService)
    assert assembly.speech.model is None
    assert not assembly.speech.model_lock.locked()
