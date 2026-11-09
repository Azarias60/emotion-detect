"""参考音频的首尾静音裁剪。

zero-shot 音色是把"参考音频 + 逐字文本"一起喂给模型的，模型会顺带把
"这段文本对应多长音频"当成语速学走。实测 zzf2 的注册音频 5.01s 里前
2.1s 是静音（按下录制键之后才开口），14 个字只占 2.9s，于是合成语速被
拉慢到 0.36 s/字（正常 0.21），听感就是拖沓、段落末尾多念。
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np
import soundfile as sf

from tts import cosyvoice3


def tone(seconds: float, sr: int = 24000, amp: float = 0.3) -> np.ndarray:
    t = np.arange(int(sr * seconds)) / sr
    return (amp * np.sin(2 * np.pi * 220 * t)).astype(np.float32)


def silence(seconds: float, sr: int = 24000) -> np.ndarray:
    return np.zeros(int(sr * seconds), dtype=np.float32)


class TrimEdgesTests(unittest.TestCase):
    def test_head_silence_is_removed(self):
        sample = np.concatenate([silence(2.1), tone(2.9)])
        out = cosyvoice3._trim_edges(sample, 24000)
        seconds = out.size / 24000
        self.assertGreater(seconds, 2.85)
        self.assertLess(seconds, 3.15)
        self.assertLess(out.size, sample.size)

    def test_clean_sample_is_left_alone(self):
        sample = tone(3.0)
        out = cosyvoice3._trim_edges(sample, 24000)
        self.assertEqual(sample.size, out.size)

    def test_trim_is_idempotent(self):
        sample = np.concatenate([silence(2.1), tone(2.9)])
        once = cosyvoice3._trim_edges(sample, 24000)
        twice = cosyvoice3._trim_edges(once, 24000)
        self.assertEqual(once.size, twice.size)

    def test_all_silence_is_left_alone(self):
        sample = silence(3.0)
        out = cosyvoice3._trim_edges(sample, 24000)
        self.assertEqual(sample.size, out.size)

    def test_tiny_result_is_treated_as_misfire(self):
        # 裁完只剩 0.26s：判为误裁，原样返回，宁可不动也不能把样本裁没
        sample = np.concatenate([silence(1.0), tone(0.2)])
        out = cosyvoice3._trim_edges(sample, 24000)
        self.assertEqual(sample.size, out.size)

    def test_low_level_recording_still_detected(self):
        # 绝对阈值会把低电平录音整段判成静音；这里按峰值相对判定
        quiet = np.concatenate([silence(1.0), tone(2.0, amp=0.004)])
        out = cosyvoice3._trim_edges(quiet, 24000)
        self.assertLess(out.size, quiet.size)
        self.assertGreater(out.size / 24000, 1.9)


class PreparedRefAudioTests(unittest.TestCase):
    def test_trimmed_copy_is_cached_and_reused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / "ref.wav"
            sf.write(str(src), np.concatenate([silence(2.1), tone(2.9)]), 24000)
            trim_dir = root / "_ref"
            with mock.patch.object(cosyvoice3, "REF_TRIM_DIR", trim_dir):
                first = cosyvoice3._prepared_ref_audio(src)
                self.assertNotEqual(src, first)
                self.assertTrue(first.is_file())
                self.assertEqual(trim_dir, first.parent)
                stamp = first.stat().st_mtime_ns

                second = cosyvoice3._prepared_ref_audio(src)
                self.assertEqual(first, second)
                self.assertEqual(stamp, second.stat().st_mtime_ns, "第二次应命中缓存")

    def test_already_trimmed_sample_is_not_copied(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / "ref.wav"
            sf.write(str(src), np.concatenate([tone(0.06), tone(2.9), tone(0.06)]), 24000)
            with mock.patch.object(cosyvoice3, "REF_TRIM_DIR", root / "_ref"):
                self.assertEqual(src, cosyvoice3._prepared_ref_audio(src))

    def test_unreadable_file_is_returned_as_is(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "broken.wav"
            src.write_bytes(b"not audio at all")
            self.assertEqual(src, cosyvoice3._prepared_ref_audio(src))

    def test_missing_file_is_returned_as_is(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "absent.wav"
            self.assertEqual(src, cosyvoice3._prepared_ref_audio(src))


if __name__ == "__main__":
    unittest.main()
