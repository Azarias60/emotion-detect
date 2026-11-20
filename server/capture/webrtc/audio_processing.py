"""Capture PCM processing using PyAV and the public SpeexDSP C API.

The device sends 48kHz Opus. Processing state belongs to one uninterrupted
capture run; consumers receive 48kHz mono PCM16. SpeexDSP suppresses noise;
FFmpeg's dynamic audio normalizer evens out quiet and loud speech.
"""
import ctypes
from ctypes.util import find_library
from fractions import Fraction
from functools import lru_cache

from av import AudioFrame, AudioResampler
from av.error import BlockingIOError, EOFError
from av.filter import Graph
import numpy as np

AUDIO_SAMPLE_RATE = 48000
AUDIO_CHANNELS = 1
AUDIO_SAMPLE_WIDTH = 2
FRAME_SAMPLES = AUDIO_SAMPLE_RATE // 50  # The documented 20ms preprocessing unit.
FRAME_BYTES = FRAME_SAMPLES * AUDIO_SAMPLE_WIDTH
SPEEX_PREPROCESS_SET_NOISE_SUPPRESS = 18
# Retain weak speech with at most 6dB noise attenuation. Gain normalization
# uses a short smoothing window and an RMS target, capped at 16x gain.
# Reproduction and individual parameter checks: tools.benchmark_capture_agc.
NOISE_SUPPRESS_DB = -6
NORMALIZER_OPTIONS = "framelen=20:gausssize=3:targetrms=0.1:maxgain=16"


@lru_cache(maxsize=1)
def load_speex_runtime():
    path = find_library("speexdsp") or find_library("libspeexdsp")
    if path is None:
        raise RuntimeError("Capture audio requires the SpeexDSP shared library (speexdsp 1.2.1)")
    library = ctypes.CDLL(path)
    library.speex_preprocess_state_init.argtypes = [ctypes.c_int, ctypes.c_int]
    library.speex_preprocess_state_init.restype = ctypes.c_void_p
    library.speex_preprocess_state_destroy.argtypes = [ctypes.c_void_p]
    library.speex_preprocess_state_destroy.restype = None
    library.speex_preprocess_run.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_int16)]
    library.speex_preprocess_run.restype = ctypes.c_int
    library.speex_preprocess_ctl.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]
    library.speex_preprocess_ctl.restype = ctypes.c_int
    return library


class _SpeexPreprocessor:
    def __init__(self):
        self._library = load_speex_runtime()
        self._state = self._library.speex_preprocess_state_init(FRAME_SAMPLES, AUDIO_SAMPLE_RATE)
        if not self._state:
            raise MemoryError("Could not allocate capture audio processor")
        # SpeexDSP AGC defaults off. Use one level controller, after denoising.
        noise_suppress = ctypes.c_int32(NOISE_SUPPRESS_DB)
        if self._library.speex_preprocess_ctl(
                self._state, SPEEX_PREPROCESS_SET_NOISE_SUPPRESS,
                ctypes.byref(noise_suppress)) != 0:
            self.close()
            raise RuntimeError("SpeexDSP does not support the configured noise suppression")

    def process(self, pcm: bytes) -> bytes:
        # Validate at the native memory boundary; the C API requires exactly
        # the sample count selected when constructing its state.
        if len(pcm) != FRAME_BYTES:
            raise ValueError("SpeexDSP requires one 20ms PCM16 mono frame")
        if not self._state:
            raise RuntimeError("Capture audio processor is closed")
        samples = np.frombuffer(pcm, dtype="<i2").astype(np.int16, copy=True)
        # The return value is VAD, not a processing status. Never drop frames.
        self._library.speex_preprocess_run(self._state, samples.ctypes.data_as(ctypes.POINTER(ctypes.c_int16)))
        return samples.astype("<i2", copy=False).tobytes()

    def close(self):
        if self._state:
            self._library.speex_preprocess_state_destroy(self._state)
            self._state = None


class CaptureAudioProcessor:
    """Normalize decoded frames and apply NS/AGC once, before all consumers."""

    def __init__(self):
        self._resampler = AudioResampler(format="s16", layout="mono", rate=AUDIO_SAMPLE_RATE)
        graph = Graph()
        source = graph.add_abuffer(sample_rate=AUDIO_SAMPLE_RATE, format="s16",
                                  layout="mono", time_base=Fraction(1, AUDIO_SAMPLE_RATE))
        normalizer = graph.add("dynaudnorm", NORMALIZER_OPTIONS)
        output = graph.add("aformat", f"sample_fmts=s16:sample_rates={AUDIO_SAMPLE_RATE}:channel_layouts=mono")
        sink = graph.add("abuffersink")
        graph.link_nodes(source, normalizer, output, sink)
        graph.configure()
        self._dsp = _SpeexPreprocessor()
        self._normalizer = graph
        self._normalizer_samples = 0
        self._pending = bytearray()

    def _normalize(self, pcm: bytes) -> bytes:
        frame = AudioFrame.from_ndarray(np.frombuffer(pcm, dtype="<i2").reshape(1, -1),
                                       format="s16", layout="mono")
        frame.sample_rate = AUDIO_SAMPLE_RATE
        frame.pts = self._normalizer_samples
        frame.time_base = Fraction(1, AUDIO_SAMPLE_RATE)
        self._normalizer_samples += len(pcm) // AUDIO_SAMPLE_WIDTH
        self._normalizer.push(frame)
        return self._drain_normalizer()

    def _drain_normalizer(self) -> bytes:
        output = []
        while True:
            try:
                frame = self._normalizer.pull()
            except (BlockingIOError, EOFError):
                return b"".join(output)
            output.append(frame.to_ndarray().astype("<i2", copy=False).tobytes())

    def _consume(self, frames) -> bytes:
        for frame in frames:
            self._pending.extend(frame.to_ndarray().astype("<i2", copy=False).tobytes())
        size = len(self._pending) // FRAME_BYTES * FRAME_BYTES
        output = b"".join(
            self._normalize(self._dsp.process(bytes(self._pending[offset:offset + FRAME_BYTES])))
            for offset in range(0, size, FRAME_BYTES)
        )
        del self._pending[:size]
        return output

    def process(self, frame) -> bytes:
        return self._consume(self._resampler.resample(frame))

    def finish(self) -> bytes:
        output = self._consume(self._resampler.resample(None))
        if self._pending:
            size = len(self._pending)
            pcm = self._dsp.process(bytes(self._pending).ljust(FRAME_BYTES, b"\0"))[:size]
            output += self._normalize(pcm)
            self._pending.clear()
        self._normalizer.push(None)
        return output + self._drain_normalizer()

    def close(self):
        self._pending.clear()
        self._dsp.close()
        self._normalizer = None
