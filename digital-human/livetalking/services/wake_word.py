"""Per-mode wake-word gate for microphone conversations.

The microphone stream stays open, but turns are dropped until the user says
the mode's wake word — both modes default to ``你好``. Typed text bypasses
the gate (a submitted input box message is deliberate). Once awake the
conversation flows normally; switching modes re-arms the gate for the newly
entered mode.
"""

from __future__ import annotations

import re

# 匹配前把转写里的空格与中英文标点都剥掉（ASR 常在名字后插逗号：
# 实测唤醒词会被 FunASR 转成"暖暖，你好。"这类带标点的串，直接匹配永远失败）
_STRIP_FOR_MATCH = re.compile(r"[\s，。！？；：、,\.!?;:~～]")
_LEADING_JUNK = re.compile(r"^[\s，。！？；：、,\.!?;:~～]+")


def _normalize(text: str) -> str:
    return _STRIP_FOR_MATCH.sub("", text or "")


def _variants(word: str) -> list[str]:
    base = word.strip()
    if base == "你好":
        # 常见口头变体；长词在前，避免"你好呀"先被"你好"命中而残留"呀"
        return ["你好呀", "您好呀", "你好", "您好"]
    if base == "暖暖":
        # 同音误听（换换/欢欢/圈圈/南南）由人设热词先行纠正——导览页跑
        # default 模式，active 人设 personas/nuan.md 带这组纠正，转写进入
        # 唤醒检查前已改成"暖暖"；这里只兜正确写法，避免"换换"误唤醒。
        return ["暖暖"]
    return [base]


class WakeWordGate:
    """Hold microphone turns back until the mode's wake word is heard."""

    def __init__(self, word_default: str = "", word_live: str = ""):
        self.words = {
            "default": (word_default or "").strip(),
            "live": (word_live or "").strip(),
        }
        self._awake = {"default": False, "live": False}

    def word_for(self, mode: str) -> str:
        return self.words.get(mode, "")

    def required(self, mode: str) -> bool:
        """True while the mode still waits for its wake word."""
        return bool(self.word_for(mode)) and not self._awake[mode]

    def open(self, mode: str) -> None:
        """显式开闸：用户主动点了麦克风（明确对话意图），无需再喊唤醒词。"""
        if mode in self._awake:
            self._awake[mode] = True

    def reset(self, mode: str | None = None) -> None:
        """Re-arm one mode (or both) — used when entering a mode."""
        if mode is None:
            self._awake = {"default": False, "live": False}
        else:
            self._awake[mode] = False

    def check(self, mode: str, transcript: str) -> tuple[bool, str, bool]:
        """Decide whether a turn may proceed.

        Returns ``(proceed, message, woke)``. When the wake word matches it
        is stripped from the utterance (``暖暖，我考砸了`` → ``我考砸了``）;
        a bare wake word becomes a simple greeting for the LLM.
        """
        word = self.word_for(mode)
        if not word or self._awake[mode]:
            return True, (transcript or "").strip(), False
        # 归一化匹配：剥空格与标点后再找唤醒词（"暖暖，你好。"→"暖暖你好"）
        text = _normalize(transcript)
        for variant in _variants(word):
            index = text.find(_normalize(variant))
            if index >= 0:
                self._awake[mode] = True
                rest = _LEADING_JUNK.sub("", text[index + len(_normalize(variant)):]).strip()
                # 唤醒词后只跟了"的/嗯"这类单字碎片时不当指令，落回问候
                message = rest if len(rest) >= 2 else "你好"
                return True, message, True
        return False, (transcript or "").strip(), False


def is_bare_greeting(transcript: str, word: str) -> bool:
    """整句只是一个问候（"你好""您好呀"）而无实质内容——
    「你好」＝新一位用户的握手：会话中单说一句你好应触发软重置。"""
    t = _normalize(transcript)
    return bool(t) and t in {_normalize(v) for v in _variants(word)}
