"""48kHz capture processing, run isolation and one requested-rate ASR WAV."""
import asyncio
from fractions import Fraction
from threading import Lock
import wave

from av import AudioFrame
from aiortc.mediastreams import MediaStreamError
import numpy as np
import pytest

from server.capture.webrtc.audio import AudioBuffer, AudioReceiver
from server.capture.webrtc.audio_processing import CaptureAudioProcessor
from server.capture.webrtc.kernel import CaptureKernel
from server.compute.speech.service import SpeechService
from server.compute.speech.types import AudioData
from server.compute.speech.types import Transcript, TranscriptSegment


def _frame(rate=48000, channels=2, pts=0, value=None):
    samples = rate // 50
    if value is None:
        signal = (1000 * np.sin(2 * np.pi * 1000 * (np.arange(samples) + pts) / rate)).astype(np.int16)
    else:
        signal = np.full(samples, value, dtype=np.int16)
    packed = np.repeat(signal, channels).reshape(1, -1)
    frame = AudioFrame.from_ndarray(packed, format="s16", layout="mono" if channels == 1 else "stereo")
    frame.sample_rate, frame.pts, frame.time_base = rate, pts, Fraction(1, rate)
    return frame


class _QueueTrack:
    def __init__(self):
        self.queue = asyncio.Queue()
        self.requests = 0

    async def recv(self):
        self.requests += 1
        frame = await self.queue.get()
        if frame is None:
            raise MediaStreamError
        return frame

    def stop(self):
        self.queue.put_nowait(None)


async def _wait_for(predicate):
    async with asyncio.timeout(2):
        while not predicate():
            await asyncio.sleep(0)


@pytest.mark.parametrize("rate,channels", [(48000, 2), (48000, 1)])
def test_receive_normalizes_duration_samples_levels_and_wav(rate, channels, tmp_path):
    scratch = tmp_path / "scratch"
    scratch.mkdir()

    async def main():
        kernel = CaptureKernel()
        track, ended = _QueueTrack(), asyncio.Event()
        receiver = AudioReceiver(track, kernel.audio_buffer, ended.set)
        try:
            for block in range(50):
                track.queue.put_nowait(_frame(rate, channels, block * (rate // 50)))
            track.queue.put_nowait(None)
            await asyncio.wait_for(ended.wait(), 2)

            pcm = np.frombuffer(kernel.audio_buffer.snapshot(), dtype="<i2")
            assert len(pcm) == 48000
            peak_hz = np.fft.rfftfreq(len(pcm), 1 / 48000)[np.argmax(abs(np.fft.rfft(pcm)))]
            assert peak_hz == 1000  # Pitch is preserved, not just a relabeled WAV.

            window = kernel.audio_window()
            assert window is not None
            assert (window.sample_rate, window.pcm) == (
                48000, kernel.audio_buffer.snapshot(),
            )
            assert len(list(scratch.iterdir())) == 0
        finally:
            await receiver.close()
            await kernel.close()

    asyncio.run(main())


def test_audio_window_is_sixty_seconds_of_48k_pcm():
    buffer = AudioBuffer()
    assert buffer.MAX_BYTES == 60 * 48000 * 2
    buffer.extend(b"\x01\x00" * (60 * 48000 + 7))
    assert len(buffer) == 60 * 48000 * 2


def test_fragmented_processing_matches_whole_stream_and_retains_partial_tail():
    signal = np.random.default_rng(13).integers(-10000, 10000, 4 * 960 + 79, dtype=np.int16)

    def process(chunks):
        processor = CaptureAudioProcessor()
        output = []
        offset = 0
        try:
            for count in chunks:
                frame = AudioFrame.from_ndarray(signal[offset:offset + count].reshape(1, -1),
                                               format="s16", layout="mono")
                frame.sample_rate = 48000
                output.append(processor.process(frame))
                offset += count
            output.append(processor.finish())
            return b"".join(output)
        finally:
            processor.close()

    expected = process([len(signal)])
    assert len(expected) == len(signal) * 2
    assert process([1, 17, 940, 22, 1510, 1429]) == expected


@pytest.mark.parametrize("transition", ["run", "pause"])
def test_processing_history_cannot_leak_previous_run_or_pause(transition):
    async def main():
        track, sink, ended = _QueueTrack(), AudioBuffer(), asyncio.Event()
        receiver = AudioReceiver(track, sink, ended.set)
        try:
            track.queue.put_nowait(_frame(value=5000))
            # recv 第二次进入即第一帧已消费完毕（与下方 requests >= 3 同一习语）。
            await _wait_for(lambda: track.requests >= 2)
            sink.clear()
            if transition == "run":
                receiver.set_audio_base_ms(1000)
            else:
                receiver.set_enabled(False)
                track.queue.put_nowait(_frame(pts=960, value=5000))
                await _wait_for(lambda: track.requests >= 3)
                receiver.set_enabled(True)
            track.queue.put_nowait(_frame(pts=48000, value=0))
            track.queue.put_nowait(None)
            await asyncio.wait_for(ended.wait(), 2)
            assert sink.snapshot() == b"\x00\x00" * 960
        finally:
            await receiver.close()

    asyncio.run(main())


def test_device_pcm_flows_directly_to_asr_without_ffmpeg(tmp_path, monkeypatch):
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    kernel = CaptureKernel()
    pcm = b"\xe8\x03" * 48000
    kernel.audio_buffer.extend(pcm)
    calls = []

    class Model:
        def generate(self, *, input, input_format=None, fs=None, **kwargs):
            assert input_format == "pcm_s16le" and fs == 16000
            converted = np.frombuffer(input, dtype="<i2")
            assert len(converted) == 16000
            assert np.max(np.abs(converted.astype(np.int32) - 1000)) <= 1
            calls.append(input)
            return [{"text": "已转写"}]

    adapter = SpeechService(lambda: None)
    adapter.model = Model()
    window = kernel.audio_window(sample_rate=16000)
    assert window is not None and window.sample_rate == 16000
    result = adapter.result(AudioData(pcm=window.pcm, sample_rate=window.sample_rate))
    assert result == Transcript((TranscriptSegment("已转写"),))
    assert len(calls) == 1 and len(list(scratch.iterdir())) == 0


def test_empty_device_audio_has_no_window():
    assert CaptureKernel().audio_window() is None
