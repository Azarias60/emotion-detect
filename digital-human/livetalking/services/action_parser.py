"""Streaming action-tag parsing for LLM replies.

The system prompt asks the LLM to open every reply with an action tag such as
``[comfort]``. This parser peels that tag off the streamed text before it can
reach TTS or the chat bubbles, reports the action once, and never stalls the
stream: as soon as the head of the reply can no longer form a tag, every
buffered character is released as speakable content.
"""

from __future__ import annotations

import re

# 动作标签 → 与 state_machine.ACTION_MAP 一致的状态段编号
ACTION_TAG_STATES = {"greet": 4, "encourage": 5, "praise": 6, "comfort": 7}
# 语义标签但不出动作：普通闲聊
NEUTRAL_TAGS = {"idle", "none"}

# 追加到各处系统提示词末尾的动作输出规则。
ACTION_PROMPT_RULES = (
    "\n动作标签规则（必须遵守）：每次回复的最开头先输出一个方括号动作标签，"
    "标签后直接写正文。标签只能选一个：[greet] 用户打招呼、问候或自我介绍时；"
    "[encourage] 用户受挫、紧张、需要鼓励打气时；[praise] 用户分享成绩进步、值得表扬时；"
    "[comfort] 用户难过、委屈、焦虑、需要安慰时；[idle] 普通闲聊或无关情绪起伏时。"
    "除此之外正文里不要再出现任何方括号。"
)

# 标签只可能出现在回复开头，最多扫描这么多个字符
MAX_TAG_SCAN = 20

# 问候语兜底：LLM 偶尔漏掉动作标签时，开场问候是唯一可确定性推断的场景
_GREETING_RE = re.compile(
    r"^\s*(你好|您好|嗨[吧呀~！？]?|哈喽|hello|hi[吧呀~！？]|在吗|早上好|中午好|下午好|晚上好|晚安|再见|拜拜)",
    re.IGNORECASE,
)

_TAG_RE = re.compile(
    r"^[\s]*[\[【（(]\s*(?:动作\s*[:：]?\s*)?"
    r"(greet|encourage|praise|comfort|idle|none)"
    r"\s*[\]】）)]",
    re.IGNORECASE,
)
_TAG_OPENERS = "[【（("


class ActionTagParser:
    """Incrementally strip one leading action tag off a streamed reply."""

    def __init__(self) -> None:
        self._head = ""
        self.action: str | None = None
        # LLM 是否给过任何标签（含 idle/none）；用于判断是否可走问候兜底
        self.saw_tag = False
        self.done = False

    def feed(self, delta: str) -> str:
        """Consume one streamed chunk, return the speakable body it yields."""
        if self.done:
            return delta
        self._head += delta
        match = _TAG_RE.match(self._head)
        if match:
            self.done = True
            self.saw_tag = True
            tag = match.group(1).lower()
            if tag in ACTION_TAG_STATES:
                self.action = tag
            body = self._head[match.end():]
            self._head = ""
            return body
        stripped = self._head.lstrip()
        if not stripped:
            # 还没出现第一个可见字符，继续等
            if len(self._head) >= MAX_TAG_SCAN:
                self.done = True
                body, self._head = self._head, ""
                return body
            return ""
        if stripped[0] not in _TAG_OPENERS or "\n" in stripped[:MAX_TAG_SCAN]:
            # 开头不可能是标签，立即放行，不增加任何首字延迟
            self.done = True
            body, self._head = self._head, ""
            return body
        if len(self._head) >= MAX_TAG_SCAN:
            # 形如 "[..." 但始终没闭合，按普通正文处理
            self.done = True
            body, self._head = self._head, ""
            return body
        return ""

    def flush(self) -> str:
        """Release anything still buffered when the stream ends."""
        self.done = True
        body, self._head = self._head, ""
        return body


def state_for_action(tag: str | None) -> int | None:
    """Map a parsed tag to its state-machine audiotype (None = no action)."""
    if not tag:
        return None
    return ACTION_TAG_STATES.get(tag)


def guess_greeting_action(message: str) -> str | None:
    """Deterministic fallback: a clear user greeting implies the greet action.

    Only used when the LLM reply carried no action tag at all; greetings are
    the one case where intent can be inferred reliably without the model.
    """
    if message and _GREETING_RE.match(message.strip()[:12]):
        return "greet"
    return None
