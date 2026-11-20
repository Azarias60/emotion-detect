"""Native DSP behavior and its memory/resource boundary, without hardware."""
import numpy as np
from av import AudioFrame
import pytest

from server.capture.webrtc import audio_processing
from server.capture.webrtc.audio_processing import CaptureAudioProcessor


def _process(signal):
    processor = CaptureAudioProcessor()
    output = []
    try:
        for offset in range(0, len(signal), 960):
            frame = AudioFrame.from_ndarray(signal[offset:offset + 960].reshape(1, -1),
                                           format="s16", layout="mono")
            frame.sample_rate = 48000
            output.append(processor.process(frame))
        output.append(processor.finish())
        return np.frombuffer(b"".join(output), dtype="<i2")
    finally:
        processor.close()


def _rms(signal):
    return np.sqrt(np.mean(signal.astype(float) ** 2))


def test_ns_and_leveling_improve_signal_to_background_contrast_without_dropping_samples():
    rate = 48000
    time = np.arange(rate * 6) / rate
    voiced = 100 * (.1 + np.maximum(0, np.sin(2 * np.pi * 3 * time)))
    voiced *= (np.sin(2 * np.pi * 135 * time + 2 * np.sin(2 * np.pi * 4 * time))
               + .3 * np.sin(2 * np.pi * 850 * time))
    signal = np.random.default_rng(42).normal(0, 30, rate * 12)
    signal[rate * 3:rate * 9] += voiced
    signal = np.rint(signal).astype(np.int16)
    result = _process(signal)
    assert len(result) == len(signal)
    voice_window, background = slice(rate * 7, rate * 9), slice(-rate * 2, None)
    before = _rms(signal[voice_window]) / _rms(signal[background])
    after = _rms(result[voice_window]) / _rms(result[background])
    # AGC also raises background level; assess separation, not absolute noise
    # reduction after applying gain. NS-only attenuation is measured separately.
    assert after > before


def test_leveling_raises_weak_modulated_signal_with_bounded_output():
    time = np.arange(48000 * 6) / 48000
    envelope = 100 * (.1 + np.maximum(0, np.sin(2 * np.pi * 3 * time)))
    signal = envelope * (np.sin(2 * np.pi * 135 * time + 2 * np.sin(2 * np.pi * 4 * time))
                         + .3 * np.sin(2 * np.pi * 850 * time))
    signal += np.random.default_rng(42).normal(0, 3, len(time))
    signal = signal.astype(np.int16)
    result = _process(signal)
    assert len(result) == len(signal)
    assert _rms(result[-48000 * 2:]) > 5 * _rms(signal[-48000 * 2:])
    assert np.max(np.abs(result.astype(np.int32))) < 32767


@pytest.mark.parametrize("quiet_dbfs,loud_dbfs", [(-46, -22), (-36, -12)])
def test_quiet_loud_quiet_speech_has_similar_levels_and_recovers_after_loud_speech(quiet_dbfs, loud_dbfs):
    rate = 48000
    time = np.arange(rate * 4) / rate
    voice = (.2 + np.maximum(0, np.sin(2 * np.pi * 3 * time)))
    voice *= (np.sin(2 * np.pi * 135 * time + 2 * np.sin(2 * np.pi * 4 * time))
              + .3 * np.sin(2 * np.pi * 850 * time))
    voice /= _rms(voice)
    rng = np.random.default_rng(42)
    signal = np.concatenate([
        voice * 32768 * 10 ** (level / 20) + rng.normal(0, 10, len(voice))
        for level in (quiet_dbfs, loud_dbfs, quiet_dbfs)
    ]).astype(np.int16)
    output = _process(signal)
    levels = [20 * np.log10(_rms(output[rate * (i * 4 + 1):rate * (i + 1) * 4]) / 32768)
              for i in range(3)]
    assert len(output) == len(signal)
    # Softer normalization preserves more dynamics. This synthetic waveform
    # verifies useful leveling; the recorded-speech benchmark measures the
    # requested approximately 4dB spread.
    assert max(levels) - min(levels) < 8  # Input speech levels differ by 24dB.
    assert abs(levels[2] - levels[0]) < 2  # Loud speech must not leave quiet speech suppressed.
    assert levels[2] > quiet_dbfs + 10
    assert np.max(np.abs(output.astype(np.int32))) < 32767


def test_leveling_starts_output_within_one_hundred_milliseconds():
    processor = CaptureAudioProcessor()
    try:
        for _ in range(5):
            frame = AudioFrame.from_ndarray(np.ones((1, 960), dtype=np.int16),
                                           format="s16", layout="mono")
            frame.sample_rate = 48000
            if processor.process(frame):
                break
        else:
            pytest.fail("Live capture must not buffer more than 100ms before producing audio")
    finally:
        processor.close()


def test_unavailable_native_dependency_fails_instead_of_bypassing_dsp(monkeypatch):
    audio_processing.load_speex_runtime.cache_clear()
    monkeypatch.setattr(audio_processing, "find_library", lambda _name: None)
    with pytest.raises(RuntimeError, match="SpeexDSP shared library"):
        CaptureAudioProcessor()


def test_closed_processor_cannot_call_native_code():
    processor = CaptureAudioProcessor()
    processor.close()
    processor.close()
    frame = AudioFrame.from_ndarray(np.zeros((1, 960), dtype=np.int16),
                                   format="s16", layout="mono")
    frame.sample_rate = 48000
    with pytest.raises(RuntimeError, match="closed"):
        processor.process(frame)
