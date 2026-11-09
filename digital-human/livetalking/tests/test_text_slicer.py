"""软边界切句器：不在词中间切断 TTS 段落。"""

from __future__ import annotations

import unittest

from services.text_slicer import SegmentSlicer


def slicer_default() -> SegmentSlicer:
    # 轻量页参数（弱标点 +4 字才切）
    return SegmentSlicer(6, 12, 20, 6, 36, 60, first_weak_min=10, weak_min=10)


def slicer_live() -> SegmentSlicer:
    # 实时口型参数：强标点(。！？)8字即切，弱标点(，、)14字才切
    return SegmentSlicer(8, 24, 32, 8, 36, 60, first_weak_min=12, weak_min=14)


def feed_all(slicer: SegmentSlicer, text: str) -> list[str]:
    segments: list[str] = []
    for ch in text:  # 逐字符流式（最苛刻场景）
        segments.extend(slicer.feed(ch))
    tail = slicer.flush()
    if tail:
        segments.append(tail)
    return segments


class SegmentSlicerTests(unittest.TestCase):
    def test_network_chunk_size_does_not_change_segments(self):
        text = "你好呀，我在听。慢慢说就好。别担心结果。我们一起想想办法。"
        def make():
            return SegmentSlicer(6, 12, 20, 20, 36, 60,
                                 first_weak_min=10, weak_min=24)
        expected = feed_all(make(), text)
        for width in (2, 7, len(text)):
            slicer = make()
            actual = []
            for i in range(0, len(text), width):
                actual.extend(slicer.feed(text[i:i + width]))
            tail = slicer.flush()
            if tail:
                actual.append(tail)
            self.assertEqual(expected, actual)
        self.assertEqual(text, "".join(expected))
        self.assertEqual(2, len(expected))

    def test_hard_limit_switches_inside_single_delta(self):
        slicer = SegmentSlicer(2, 4, 6, 10, 12, 14)
        actual = slicer.feed("甲。" + "乙" * 28)
        self.assertEqual(["甲。", "乙" * 14, "乙" * 14], actual)

    def test_hard_cap_backtracks_to_last_punctuation(self):
        # 用户实测案例："伙伴"被硬上限劈成"伙…伴"。弱标点没攒够门槛
        # 暂时不切；到硬上限时必须回退到缓冲里最近的标点处切，不劈词
        slicer = SegmentSlicer(6, 12, 20, 6, 36, 30,
                               first_weak_min=10, weak_min=24)
        text = "今天天气真不错，" + "乙" * 23  # 8 字逗号 + 无标点长串
        actual = slicer.feed(text)
        self.assertEqual(["今天天气真不错，"], actual)
        self.assertEqual("乙" * 23, slicer.flush())
        self.assertEqual(text, "今天天气真不错，" + "乙" * 23)

    def test_backtrack_ignores_punctuation_too_early(self):
        # 缓冲里唯一的标点太靠前（切出去不足段长下限）：仍按硬上限
        # 词中兜底切——段长过短会诱发 zero-shot 续写幻觉
        slicer = SegmentSlicer(6, 8, 12, 6, 36, 30,
                               first_weak_min=24, weak_min=24)
        text = "好，" + "乙" * 10
        self.assertEqual([text], slicer.feed(text))
        self.assertEqual("", slicer.flush())

    def test_word_never_split_when_punctuation_follows(self):
        # 用户实际遇到的案例："听你说|话的小伙伴" 劈词
        text = "我会成为一个愿意听你说心里话的小伙伴。"
        segments = feed_all(slicer_default(), text)
        self.assertEqual(1, len(segments))
        self.assertEqual(text, segments[0])
        # 每个切口都落在标点上（尾巴除外）
        for seg in segments[:-1]:
            self.assertTrue(seg[-1] in "，。！？；：、,.!?;:")

    def test_overflow_pastes_never_split_words(self):
        # 超过首段上限(12)仍无标点：等标点，绝不词中切断
        text = "我想变成一个愿意听你说心里话的小伙伴"
        slicer = slicer_default()
        segments = feed_all(slicer, text + "。")
        # 无标点部分不会被提前吐出
        self.assertEqual([text + "。"], segments)

    def test_normal_punctuation_cuts_preserved(self):
        segments = feed_all(
            slicer_default(), "好的呀，我在听呢。还有什么想聊的吗？"
        )
        self.assertEqual(
            ["好的呀，我在听呢。", "还有什么想聊的吗？"], segments
        )

    def test_hard_cap_splits_punctuation_free_runs(self):
        text = "一" * 60  # 完全无标点
        segments = feed_all(slicer_default(), text)
        self.assertGreaterEqual(len(segments), 1)
        self.assertEqual(60, sum(len(s) for s in segments))
        self.assertLessEqual(max(len(s) for s in segments), 60)

    def test_first_segment_thresholds_respected(self):
        # 首段最少 6 字：短句读不触发切段，攒到标点且 ≥6 才切
        segments = feed_all(slicer_default(), "好的，没问题。")
        self.assertEqual(["好的，没问题。"], segments)

    def test_live_thresholds(self):
        segments = feed_all(
            slicer_live(), "好的呀，我在听呢。还有什么想聊的吗？"
        )
        self.assertEqual(
            ["好的呀，我在听呢。", "还有什么想聊的吗？"], segments
        )

    def test_live_commas_do_not_fragment(self):
        # 用户实测案例：每个逗号都被切成 9 字小段——现在弱标点要攒够 14 字
        text = "我今天心情挺平静的，像晒过太阳的被子，暖暖的，你呢？"
        segments = feed_all(slicer_live(), text)
        # 从 4 个 9 字碎片变为 2 个自然段，所有切口都在标点上
        self.assertEqual(
            ["我今天心情挺平静的，像晒过太阳的被子，", "暖暖的，你呢？"],
            segments,
        )
        self.assertNotIn("我今天心情挺平静的，", segments)

    def test_live_long_comma_run_cuts_when_long_enough(self):
        text = "你开口让我夸你这份坦率特别可爱，" + "另外你今天穿的衣服也好看，" + "还有你的笑容很有感染力。"
        segments = feed_all(slicer_live(), text)
        for seg in segments[:-1]:
            self.assertTrue(
                seg[-1] in "。！？!?.;，、；：,:",
                f"切口必须在标点: {seg!r}",
            )
            if seg[-1] in "，、；：,:":
                self.assertGreaterEqual(
                    len(seg), 14, f"弱标点切口段长需≥14: {seg!r}"
                )

    def test_strong_ending_cuts_at_min(self):
        segments = feed_all(slicer_live(), "我今天考试砸了。没事的。")
        self.assertEqual(["我今天考试砸了。", "没事的。"], segments)

    def test_all_cuts_on_punctuation_or_tail(self):
        # 长回复：所有段边界必须落在标点上（尾巴除外）
        text = (
            "听起来你今天挺累的，先深呼吸一下。辛苦了，"
            "能跟我说说今天发生什么了吗？我会一直陪着你的。"
        )
        segments = feed_all(slicer_live(), text)
        for seg in segments[:-1]:
            self.assertTrue(seg[-1] in "，。！？；：、,.!?;:", repr(seg))
        self.assertEqual(text, "".join(segments))


if __name__ == "__main__":
    unittest.main()
