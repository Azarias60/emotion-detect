# -*- coding: utf-8 -*-
"""流式文本软边界切句（搬运自 LiveTalking services/text_slicer.py，原样保留）。

流式回复在标点处切成自然的韵律单元送给 TTS：强标点（。！？）攒够下限
即切；弱标点（，、；）要攒够更长才切，否则每个逗号都会把回复切碎。
超硬上限时回退到缓冲里最近的标点处切，词永远不会被劈开。
"""

STRONG_ENDINGS = set("。！？!?.;")
WEAK_ENDINGS = set("，、；：,:")


class SegmentSlicer:
    """喂入流式分片，feed 返回已完成的可用段落。"""

    def __init__(
        self,
        first_min: int,
        first_max: int,
        first_hard: int,
        min_len: int,
        max_len: int,
        hard_len: int,
        *,
        first_weak_min: int | None = None,
        weak_min: int | None = None,
    ):
        self._first = (
            first_min,
            first_weak_min if first_weak_min is not None else first_min,
            first_max,
            first_hard,
        )
        self._normal = (
            min_len,
            weak_min if weak_min is not None else min_len,
            max_len,
            hard_len,
        )
        self._buffer = ""
        self._emitted_first = False

    @property
    def _limits(self) -> tuple[int, int, int, int]:
        """(强标点下限, 弱标点下限, 软上限, 硬上限)。"""
        return self._first if not self._emitted_first else self._normal

    def _cut(self, text: str) -> str:
        self._emitted_first = True
        return text

    def feed(self, text: str) -> list[str]:
        segments: list[str] = []
        for char in text:
            strong_min, weak_min, _maximum, hard = self._limits
            self._buffer += char
            if char in STRONG_ENDINGS and len(self._buffer) >= strong_min:
                segments.append(self._cut(self._buffer))
                self._buffer = ""
            elif char in WEAK_ENDINGS and len(self._buffer) >= weak_min:
                segments.append(self._cut(self._buffer))
                self._buffer = ""
            elif len(self._buffer) >= hard:
                # 超硬上限：回退到缓冲里最近的标点处切（词安全）
                cut = len(self._buffer)
                for i in range(len(self._buffer) - 1, -1, -1):
                    if self._buffer[i] in STRONG_ENDINGS or self._buffer[i] in WEAK_ENDINGS:
                        cut = i + 1
                        break
                if cut >= len(self._buffer):
                    cut = len(self._buffer)
                segments.append(self._cut(self._buffer[:cut]))
                self._buffer = self._buffer[cut:]
        return segments

    def flush(self) -> list[str]:
        """流结束：把剩余缓冲整段放出。"""
        if not self._buffer:
            return []
        rest, self._buffer = self._buffer, ""
        return [self._cut(rest)]
