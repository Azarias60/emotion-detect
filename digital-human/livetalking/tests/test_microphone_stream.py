import threading
import unittest

import numpy as np

from server.microphone_stream import (
    ResidentWhisper,
    StreamingEnergyVAD,
    drop_hallucination,
)


class StreamingEnergyVADTests(unittest.TestCase):
    @staticmethod
    def _block(amplitude: int) -> bytes:
        return np.full(512, amplitude, dtype="<i2").tobytes()

    def test_silence_does_not_start_a_turn(self):
        vad = StreamingEnergyVAD()
        events = []
        for _ in range(40):
            events.extend(vad.feed(self._block(0)))
        self.assertEqual([], events)
        self.assertFalse(vad.speaking)

    def test_voice_then_pause_emits_one_complete_utterance(self):
        vad = StreamingEnergyVAD(start_ms=120, silence_ms=600)
        events = []
        for _ in range(4):
            events.extend(vad.feed(self._block(7000)))
        self.assertEqual(["speech_started"], [event.kind for event in events])

        for _ in range(19):
            events.extend(vad.feed(self._block(0)))
        self.assertEqual(
            ["speech_started", "speech_stopped"],
            [event.kind for event in events],
        )
        self.assertGreater(events[-1].audio.size, 16000 // 2)
        self.assertFalse(vad.speaking)

    def test_max_duration_closes_a_turn_without_silence(self):
        vad = StreamingEnergyVAD(
            start_ms=32,
            silence_ms=5000,
            max_utterance_s=0.10,
        )
        events = []
        for _ in range(4):
            events.extend(vad.feed(self._block(8000)))
        self.assertIn("speech_stopped", [event.kind for event in events])
        self.assertFalse(vad.speaking)


class FakeWhisperModel:
    def __init__(self, segments, text=""):
        self.segments = segments
        self.text = text
        self.calls = []

    def transcribe(self, audio, **kwargs):
        self.calls.append(kwargs)
        return {"segments": self.segments, "text": self.text}


def _make_asr(segments, text=""):
    asr = ResidentWhisper.__new__(ResidentWhisper)
    asr.model_name = "small"
    asr._model = FakeWhisperModel(segments, text)
    asr._lock = threading.RLock()
    return asr


def _seg(text, no_speech=0.05, logprob=-0.3):
    return {"text": text, "no_speech_prob": no_speech, "avg_logprob": logprob}


class WhisperHallucinationTests(unittest.TestCase):
    def test_watermark_transcripts_are_dropped(self):
        for text in ("字幕by索兰娅", "字幕由某某提供", "谢谢观看",
                     "Thanks for watching!"):
            self.assertEqual("", drop_hallucination(text), text)

    def test_normal_transcripts_pass(self):
        for text in ("暖暖我今天考砸了", "今天天气不错", "HELLO"):
            self.assertEqual(text, drop_hallucination(text))

    def test_silent_and_low_confidence_segments_are_filtered(self):
        asr = _make_asr([
            _seg("字幕by索兰娅", no_speech=0.9),        # 静音段 → 丢
            _seg("你好呀", logprob=-1.5),                # 低置信 → 丢
            _seg("暖暖我回来了", no_speech=0.1),         # 正常 → 留
        ])
        self.assertEqual("暖暖我回来了", asr.transcribe_pcm(np.zeros(1600, "<i2")))

    def test_all_filtered_yields_empty(self):
        asr = _make_asr([_seg("字幕by索兰娅", no_speech=0.95)])
        self.assertEqual("", asr.transcribe_pcm(np.zeros(1600, "<i2")))

    def test_normal_speech_unaffected(self):
        asr = _make_asr([_seg("今天心情很好")])
        self.assertEqual("今天心情很好", asr.transcribe_pcm(np.zeros(1600, "<i2")))

    def test_blacklist_still_applies_to_confident_segments(self):
        # 置信度正常但内容是水印句式 → 仍然丢弃
        asr = _make_asr([_seg("请不吝点赞 订阅频道", no_speech=0.1, logprob=-0.2)])
        self.assertEqual("", asr.transcribe_pcm(np.zeros(1600, "<i2")))

    def test_previous_text_conditioning_disabled(self):
        asr = _make_asr([_seg("你好")])
        asr.transcribe_pcm(np.zeros(1600, "<i2"))
        kwargs = asr._model.calls[0]
        self.assertIs(False, kwargs.get("condition_on_previous_text"))

    def test_no_segments_falls_back_to_text(self):
        asr = _make_asr([], text="暖暖你好")
        self.assertEqual("暖暖你好", asr.transcribe_pcm(np.zeros(1600, "<i2")))


if __name__ == "__main__":
    unittest.main()
