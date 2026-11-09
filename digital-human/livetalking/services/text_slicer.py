"""Stream text into speakable TTS segments without splitting words.

Streaming replies are cut at punctuation so each TTS segment is a natural
prosody unit. Two punctuation tiers keep the rhythm natural: strong endings
(。！？) end a thought and cut as soon as the minimum length is reached,
while weak endings (，、；) only cut once the segment is long enough to stand
alone — otherwise every comma would fragment the reply into choppy mini
segments. When no punctuation arrives, the cut waits up to the hard cap;
at the hard cap it still backtracks to the latest in-buffer punctuation so
words are never split ("听你说|话的小伙伴") unless the whole buffer is
punctuation-free.
"""

from __future__ import annotations

STRONG_ENDINGS = set("。！？!?.;")
WEAK_ENDINGS = set("，、；：,:")


class SegmentSlicer:
    """Feed streamed deltas; ``feed`` returns completed speakable segments."""

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
    ) -> None:
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
        """(strong_min, weak_min, max, hard) for the current segment."""
        return self._first if not self._emitted_first else self._normal

    def feed(self, text: str) -> list[str]:
        segments: list[str] = []
        for char in text:
            # 同一网络 delta 可能包含多句；每次切段后立即改用后续段阈值。
            strong_min, weak_min, _maximum, hard = self._limits
            self._buffer += char
            if char in STRONG_ENDINGS and len(self._buffer) >= strong_min:
                segments.append(self._buffer)
                self._buffer = ""
                self._emitted_first = True
            elif char in WEAK_ENDINGS and len(self._buffer) >= weak_min:
                segments.append(self._buffer)
                self._buffer = ""
                self._emitted_first = True
            elif len(self._buffer) >= hard:
                # 超硬上限时优先回退到缓冲里最近的标点处切（词安全），
                # 只有整段始终没有标点才允许词中切断。否则攒段的等待
                # 会让硬切落在词中间 —— 用户听到"伙伴"被读成"伙…伴"，
                # 后半段还要独自付一次 TTS 首包延迟。
                cut = self._last_punct_index(strong_min)
                if cut is None:
                    segments.append(self._buffer)
                    self._buffer = ""
                else:
                    segments.append(self._buffer[: cut + 1])
                    self._buffer = self._buffer[cut + 1 :]
                self._emitted_first = True
        return segments

    def _last_punct_index(self, min_len: int) -> int | None:
        """缓冲内最后一个"切下去段长 ≥ min_len"的标点下标；没有则 None。

        位置太靠前的标点（切出去的段太短）跳过——段长不足会诱发
        zero-shot 续写幻觉，也可能造成首段过碎。
        """
        for index in range(len(self._buffer) - 1, -1, -1):
            if index + 1 < min_len:
                break
            if self._buffer[index] in STRONG_ENDINGS | WEAK_ENDINGS:
                return index
        return None

    def flush(self) -> str:
        """Return (and clear) the tail that never reached a boundary."""
        tail, self._buffer = self._buffer, ""
        if tail:
            self._emitted_first = True
        return tail
