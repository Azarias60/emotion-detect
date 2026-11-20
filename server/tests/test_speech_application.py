"""Speech transcription pipeline and FunASR adapter contract tests."""

import pytest

from server.compute.speech.speech.adapters.funasr import FunASRTextReader
from server.compute.speech.speech.application import transcribe_pcm
from server.compute.speech.types import AudioData, Transcript, TranscriptSegment
from server.compute.speech.service import SpeechService

PCM = b"\x01\x00" * 16000


class _FakeModel:
    """Model-slot test double shaped like the FunASR AutoModel."""

    def __init__(self, raw_text):
        self.raw_text = raw_text
        self.payloads = []

    def generate(self, **kwargs):
        self.payloads.append((kwargs["input"], kwargs["fs"]))
        if self.raw_text is None:
            return []
        return [{"text": self.raw_text}]


@pytest.mark.parametrize("raw,expected", [
    ("<|zh|>甲。乙。丙", ("甲。 乙。", "丙")),
    ("hello", ("hello",)),
    ("", ()),
    (None, ()),
])
def test_pipeline_returns_owned_transcript_from_model_slot(raw, expected):
    model = _FakeModel(raw)
    result = transcribe_pcm(model, PCM, 16000)
    assert model.payloads == [(PCM, 16000)]
    if expected:
        assert isinstance(result, Transcript)
        assert tuple(segment.text for segment in result.segments) == expected
        assert all(isinstance(segment, TranscriptSegment) for segment in result.segments)
        assert all(not hasattr(segment, "emotion") for segment in result.segments)
    else:
        assert result is None


def test_pipeline_propagates_model_failure():
    class BrokenModel:
        def generate(self, **kwargs):
            raise RuntimeError("ASR unavailable")
    with pytest.raises(RuntimeError, match="ASR unavailable"):
        transcribe_pcm(BrokenModel(), PCM, 16000)


def test_funasr_adapter_owns_model_payload_translation():
    class Model:
        def __init__(self):
            self.calls = []
        def generate(self, **kwargs):
            self.calls.append(kwargs)
            return [{"text": "<|zh|>甲。乙。丙"}, {"text": "ignored"}]

    model = Model()
    reader = FunASRTextReader(model)
    assert reader.recognize(PCM, 16000) == "<|zh|>甲。乙。丙"
    assert model.calls == [{
        "input": PCM, "input_format": "pcm_s16le", "fs": 16000,
        "language": "auto", "use_itn": True,
    }]


@pytest.mark.model
def test_funasr_pcm_bytes_match_wav_file_samples():
    """funasr 的裸 PCM 字节输入与读取 16k WAV 文件得到同一 float32 样本。

    锁定的 funasr 1.4.16（PyPI）中 ``load_bytes`` 只接受字节并自动区分
    容器与裸 PCM；旧的双参格式提示是 git-master API，不在锁定依赖内。
    """
    import numpy as np
    from funasr.utils.load_utils import load_bytes

    decoded = load_bytes(PCM)
    expected = np.frombuffer(PCM, dtype="<i2").astype(np.float32) / 32768.0
    np.testing.assert_array_equal(decoded, expected)


def test_audio_adapter_skips_asr_for_missing_audio():
    adapter = SpeechService(lambda: None)
    assert adapter.result(None) is None
    assert adapter.result(AudioData(pcm=b"", sample_rate=16000)) is None


def test_audio_adapter_transcribes_given_pcm_under_domain_lock():
    class Voice:
        def __init__(self):
            self.calls = []
        def generate(self, **kwargs):
            self.calls.append(kwargs)
            return [{"text": "<|zh|>甲。乙。"}]

    adapter = SpeechService(lambda: None)
    voice = Voice()
    adapter.model = voice
    result = adapter.result(AudioData(pcm=PCM, sample_rate=16000))
    assert result == Transcript((TranscriptSegment("甲。 乙。"),))
    assert voice.calls[0]["input_format"] == "pcm_s16le"


def test_audio_adapter_requires_model_slot_for_nonempty_audio():
    adapter = SpeechService(lambda: None)
    with pytest.raises(RuntimeError, match="SpeechService is not loaded"):
        adapter.result(AudioData(pcm=PCM, sample_rate=16000))
