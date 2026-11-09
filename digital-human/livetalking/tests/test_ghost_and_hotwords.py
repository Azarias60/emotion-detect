from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from server import personas
from server.microphone_stream import is_ghost_transcript


class HotwordCorrectionTests(unittest.TestCase):
    def _persona_with_hotwords(self, hotwords: str) -> personas.Persona:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "p.md"
            path.write_text(
                f"---\nname: 测试\nhotwords: {hotwords}\n---\n提示词",
                encoding="utf-8",
            )
            persona = personas.parse_persona(path)
        self.assertIsNotNone(persona)
        return persona

    def test_correction_map_parses_pairs(self):
        persona = self._persona_with_hotwords("换换=暖暖, 欢欢=暖暖, 空格 = 保留")
        self.assertEqual(
            {"换换": "暖暖", "欢欢": "暖暖", "空格": "保留"},
            persona.correction_map(),
        )

    def test_correct_replaces_mishearing(self):
        persona = self._persona_with_hotwords("换换=暖暖, 欢欢=暖暖")
        self.assertEqual(
            "暖暖你好，我是暖暖",
            persona.correct("换换你好，我是欢欢"),
        )

    def test_longest_first_replacement(self):
        persona = self._persona_with_hotwords("魔镜=魔镜啊, 魔镜女巫=魔镜女巫大人")
        self.assertEqual(
            "魔镜女巫大人好",
            persona.correct("魔镜女巫好"),
        )

    def test_no_hotwords_is_noop(self):
        persona = self._persona_with_hotwords("")
        self.assertEqual("原样", persona.correct("原样"))


class GhostTranscriptTests(unittest.TestCase):
    REPLY = "听起来你今天挺累的，先深呼吸一下，缓缓吧。辛苦了，能跟我说说今天发生什么了吗？"

    def test_echo_of_recent_reply_tail_is_suppressed(self):
        self.assertTrue(
            is_ghost_transcript(
                "辛苦了，能跟我说说今天发生什么了吗",
                self.REPLY,
                2.0,
            )
        )

    def test_normal_speech_passes(self):
        self.assertFalse(
            is_ghost_transcript(
                "我今天在学校遇到一件特别烦心的事情",
                self.REPLY,
                2.0,
            )
        )

    def test_old_window_passes(self):
        self.assertFalse(
            is_ghost_transcript(
                "辛苦了，能跟我说说今天发生什么了吗",
                self.REPLY,
                30.0,
            )
        )

    def test_short_interjection_passes(self):
        # "谢谢你" is natural user speech even if the reply contains it.
        self.assertFalse(is_ghost_transcript("谢谢你", self.REPLY, 1.0))

    def test_no_reply_text_passes(self):
        self.assertFalse(is_ghost_transcript("随便说点什么都很长的一句话", None, 1.0))

    def test_echo_of_reply_head_is_suppressed(self):
        # 回声可能来自回复的**开头**：数字人还在说，麦克风把前几个字收回去。
        # 只比对尾部会漏掉这种回声，数字人于是自问自答。
        head = "听起来你今天挺累的，先深呼吸一下"
        self.assertTrue(is_ghost_transcript(head, self.REPLY, 0.0))

    def test_live_window_covers_late_echo(self):
        tail = "辛苦了，能跟我说说今天发生什么了吗"
        # 默认 6s 窗口外（轻量页直推 PCM，出声几乎无延迟）
        self.assertFalse(is_ghost_transcript(tail, self.REPLY, 12.0))
        # 实时口型：段落要先排队合成再经 Wav2Lip 管线，窗口放宽到 20s
        self.assertTrue(is_ghost_transcript(tail, self.REPLY, 12.0, window=20.0))

    def test_novel_speech_still_passes_in_live_window(self):
        self.assertFalse(
            is_ghost_transcript(
                "我今天在学校遇到一件特别烦心的事情",
                self.REPLY,
                0.0,
                window=20.0,
            )
        )


if __name__ == "__main__":
    unittest.main()
