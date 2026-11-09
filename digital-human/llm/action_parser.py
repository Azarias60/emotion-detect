# -*- coding: utf-8 -*-
"""流式动作/模块标签剥离（搬运自 LiveTalking services/action_parser.py）。

本项目标签体系（按段库裁剪）：
- 动作标签：[greet] 用户打招呼/问候/自我介绍（段库有 greet 段）
            [idle]  普通闲聊（无动作）
- 模块标签：[MODULE:key] 用户诉求与某检测模块相关（health/fatigue/
            emotion/companion），由调用方做语音提示+确认跳转

解析器把开头的标签从流式文本里剥掉（不进 TTS/聊天气泡），
只上报一次，绝不阻塞流：一旦开头不可能再构成标签，缓存字符全部放行。
"""

import re

# 段库里真实存在的动作段（greet）。其余词汇出现时静默降级为"不播动作"。
ACTION_TAG_STATES = {"greet": "greet"}
# 语义标签但不出动作：普通闲聊
NEUTRAL_TAGS = {"idle", "none"}

# 模块标签（本项目的模块推进机制）
MODULE_KEYS = ("health", "fatigue", "emotion", "companion")
_MODULE_RE = re.compile(
    r"^\s*[\[【（(]\s*MODULE\s*[:：]?\s*(" + "|".join(MODULE_KEYS) + r")\s*[\]】）)]",
    re.IGNORECASE,
)

# 追加到系统提示词末尾的标签输出规则
ACTION_PROMPT_RULES = (
    "\n标签规则（必须遵守）：每次回复的最开头先输出标签，标签后直接写正文。"
    "动作标签只能选一个：[greet] 用户打招呼、问候或自我介绍时；[idle] 普通闲聊时。"
    "当用户的诉求明显与某个检测模块相关时，在正文之后另起一行输出模块标签"
    "[MODULE:key]，key 只能取 health / fatigue / emotion / companion 之一，一次最多一个；"
    "用户只是闲聊、打招呼、道别时不输出模块标签。除此之外正文里不要再出现任何方括号。"
)

# 标签只可能出现在开头，最多扫描这么多个字符
MAX_TAG_SCAN = 24

_TAG_RE = re.compile(
    r"^[\s]*[\[【（(]\s*(?:动作\s*[:：]?\s*)?"
    r"(greet|idle|none)"
    r"\s*[\]】）)]",
    re.IGNORECASE,
)
_TAG_OPENERS = "[【（("


class ActionTagParser:
    """增量剥离开头的一个动作标签；同时识别并剥离正文中的模块标签行。"""

    def __init__(self) -> None:
        self._head = ""
        self.action: str | None = None
        self.saw_tag = False
        self.done = False
        self.module: str | None = None
        self._line_buf = ""  # 模块标签行缓冲（未完结的行）

    def _filter_module(self, text: str) -> str:
        """把成行的模块标签从正文里剥掉（只上报，不进 TTS/气泡）。

        只有"以 [ 开头且尚未完结"的行会被扣住等判定，其余文本立即放行——
        无换行的正文不会因此延迟。
        """
        if not text:
            return text
        self._line_buf += text
        idx = self._line_buf.rfind("\n")
        if idx < 0:
            # 一行都还没完结：只有可能是模块标签时才扣住
            if self._line_buf.lstrip().startswith("[") and len(self._line_buf) <= MAX_TAG_SCAN:
                return ""
            out, self._line_buf = self._line_buf, ""
            return out
        complete, rest = self._line_buf[: idx + 1], self._line_buf[idx + 1:]
        self._line_buf = ""
        out: list[str] = []
        for line in complete.split("\n")[:-1]:
            m = _MODULE_RE.match(line)
            if m and self.module is None:
                self.module = m.group(1).lower()
                # 标签行整行丢弃（连同它的换行符）
            else:
                out.append(line + "\n")
        if rest.lstrip().startswith("[") and len(rest) <= MAX_TAG_SCAN:
            self._line_buf = rest  # 尾部残余行可能是标签，扣住
        else:
            out.append(rest)
        return "".join(out)

    def feed(self, delta: str) -> str:
        """消费一个流式分片，返回可播报的正文。"""
        if self.done:
            return self._filter_module(delta)
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
            return self._filter_module(body)
        stripped = self._head.lstrip()
        if not stripped:
            # 还没出现第一个可见字符，继续等
            if len(self._head) >= MAX_TAG_SCAN:
                self.done = True
                body, self._head = self._head, ""
                return self._filter_module(body)
            return ""
        if stripped[0] not in _TAG_OPENERS or "\n" in stripped[:MAX_TAG_SCAN]:
            # 开头不可能是标签，立即放行，不增加任何首字延迟
            self.done = True
            body, self._head = self._head, ""
            return self._filter_module(body)
        if len(self._head) >= MAX_TAG_SCAN:
            # 形如 "[..." 但始终没闭合，按普通正文处理
            self.done = True
            body, self._head = self._head, ""
            return self._filter_module(body)
        return ""

    def flush(self) -> str:
        """流结束时把剩余缓存放行（标签未闭合的残余 / 行尾的模块标签）。"""
        rest_head, self._head = self._head, ""
        self.done = True
        if rest_head:
            body = self._filter_module(rest_head)
            tail, self._line_buf = self._line_buf, ""
            if tail:
                m = _MODULE_RE.match(tail)
                if m and self.module is None:
                    self.module = m.group(1).lower()
                else:
                    body += tail
            return body
        tail, self._line_buf = self._line_buf, ""
        if not tail:
            return ""
        m = _MODULE_RE.match(tail)
        if m and self.module is None:
            self.module = m.group(1).lower()
            return ""
        return tail
