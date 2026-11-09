# -*- coding: utf-8 -*-
"""多轮对话记忆（搬运自 LiveTalking services/llm_memory.py，原样保留）。

按会话隔离的滚动窗口：保留最近若干轮 + 总字数上限，超出丢最旧的。
历史里绝不留下动作/模块标签——LLM 看到自己以前"说过" [greet]，
会学着在正文里也塞方括号。
"""

import re
from collections import deque

# 开头的方括号标签（动作标签或模块标签），最多扫 12 个字符
_LEADING_TAG = re.compile(r"^\s*[\[【（(][^\]】）)]{0,12}[\]】）)]\s*")


def strip_leading_tag(text: str) -> str:
    """去掉回复开头的标签（如 [greet]、[MODULE:fatigue]）。"""
    return _LEADING_TAG.sub("", text or "", count=1)


class ConversationMemory:
    """滚动窗口的多轮对话历史。"""

    def __init__(self, max_turns: int = 8, max_chars: int = 2000):
        # max_turns 是"一问一答"的轮数，实际保留 2*max_turns 条消息
        self.max_turns = max(1, int(max_turns))
        self.max_chars = max(200, int(max_chars))
        self._items: deque[tuple[str, str]] = deque()

    def append(self, role: str, content: str) -> None:
        content = strip_leading_tag(content or "")
        if not content:
            return
        self._items.append((role, content))
        self._trim()

    def _trim(self) -> None:
        while len(self._items) > self.max_turns * 2:
            self._items.popleft()
        while len(self._items) > 2 and self._chars() > self.max_chars:
            self._items.popleft()

    def _chars(self) -> int:
        return sum(len(c) for _, c in self._items)

    def messages(self) -> list[dict]:
        return [{"role": role, "content": content} for role, content in self._items]