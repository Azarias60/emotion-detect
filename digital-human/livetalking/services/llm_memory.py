"""多轮对话记忆。

之前 `LLMService.stream_text` 每轮只发 `[system, user]`，所以数字人完全没有
上下文 —— 用户说"我叫小明"，下一句问"我叫什么"，它答不上来。

这里做一个**按会话隔离的滚动窗口**：保留最近若干轮 + 总字数上限，超出丢最旧的。
挂在会话对象上（`session.llm_memory`），会话销毁时随之回收，不需要全局注册表，
也不会串会话。

两条链路各挂一份：
- 实时口型页 → `BaseAvatar.llm_memory`（每个 WebRTC 会话一份）
- 轻量默认页 → `MicrophoneConnection.llm_memory`（每个连接一份）
"""

from __future__ import annotations

import re
from collections import deque

# 动作标签只出现在回复开头，最多扫这么多字符
_LEADING_TAG = re.compile(r"^\s*[\[【（(][^\]】）)]{0,12}[\]】）)]\s*")


def strip_leading_tag(text: str) -> str:
    """去掉回复开头的动作标签（如 ``[comfort]``）。

    历史里绝不能留下标签：LLM 看到自己以前"说过" ``[greet]``，会学着在
    正文里也塞方括号。
    """
    return _LEADING_TAG.sub("", text or "", count=1)


class ConversationMemory:
    """滚动窗口的多轮对话历史。"""

    def __init__(self, max_turns: int = 8, max_chars: int = 2000):
        # max_turns 是"一问一答"的轮数，所以实际保留 2*max_turns 条消息
        self.max_turns = max(1, int(max_turns))
        self.max_chars = max(200, int(max_chars))
        self._items: deque[tuple[str, str]] = deque()

    def append(self, role: str, content: str) -> None:
        content = (content or "").strip()
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

    def clear(self) -> None:
        """清空会话历史（用户刷新口令/新用户接入时用）。"""
        self._items.clear()

    def compress(self, summarizer=None) -> str:
        """会话压缩：历史过长时，用最近若干轮的滚动摘要替代全部历史。

        规则：history_turns 累计超过 6 轮后，把最早的 (轮数-4) 轮折叠成一条
        「此前聊过」摘要消息（summarizer 可由调用方提供，默认取每轮首句
        截断拼接，零依赖零延迟）。返回摘要文本（无压缩则为空串）。
        """
        turns = len(self._items) // 2
        if turns <= 6:
            return ""
        drop_pairs = min(turns - 4, 8)
        dropped = []
        for _ in range(drop_pairs):
            if len(self._items) < 2:
                break
            user, assistant = self._items.popleft(), self._items.popleft()
            dropped.append(f"用户说「{user[1][:40]}」；回应「{assistant[1][:40]}」")
        if summarizer is not None:
            try:
                summary = summarizer(dropped) or ""
            except Exception:
                summary = ""
        else:
            summary = "；".join(dropped)[:400]
        self._items.appendleft(("system", f"此前聊过：{summary}"))
        self._trim()
        return summary

    def clear(self) -> None:
        self._items.clear()

    def turns(self) -> int:
        return len(self._items) // 2

    def __len__(self) -> int:
        return len(self._items)

    def __repr__(self) -> str:  # pragma: no cover - 仅调试用
        return f"ConversationMemory(turns={self.turns()}, chars={self._chars()})"
