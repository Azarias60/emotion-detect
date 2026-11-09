# -*- coding: utf-8 -*-
"""资料标签（[DOC: 标题 | 要点;要点]）的流式剥离。

LLM 把"需要完整展示的重要资料"放在回复末尾的机读标签里，例如：
  ...建议你早点休息。
  [DOC: 疲劳改善建议 | 每工作1小时起身活动5分钟;23点前入睡保证7小时]

标签绝不能进 TTS（会被念出来）也不能留在聊天气泡里。本剥离器逐块处理
流式文本：遇到完整标签就提取出来交给回调，同时把标签从正文中删掉；
遇到"可能是标签开头"的尾巴（如 `[`、`[D`、`[DO`、`[DOC: ...`）就先攒着，
最多攒 5 个字符，不会造成可感知的首字延迟。
"""
from __future__ import annotations

import re

DOC_PREFIX = "[DOC:"
OPENERS = ("[", "[D", "[DO", "[DOC", "[DOC:")
_MAX_HOLD = len(DOC_PREFIX)

# 完整标签：[DOC: 标题 | 要点1;要点2;要点3]
_DOC_RE = re.compile(r"\[DOC:\s*([^|\]]+?)\s*\|\s*([^\]]+)\]")

# 追加到系统提示词末尾的资料输出规则
DOC_PROMPT_RULES = (
    "\n资料标签规则：当用户询问检测结果、健康数据、指标解释等需要完整展示的重要资料时，"
    "在回复的最后单独一行输出：[DOC: 资料标题 | 要点1;要点2;要点3]。"
    "要点 6~10 条，每条一句话、具体可用（含数值与结论），不要空话。"
    "普通闲聊不要输出资料标签。"
)


class DocTagStripper:
    """流式剥离 [DOC: ...] 标签；完整标签触发 on_doc(doc_dict)。"""

    def __init__(self, on_doc=None) -> None:
        self.on_doc = on_doc
        self._buf = ""
        # LLM 是否主动输出过 [DOC:...]（主动输出的结构化最优，自动推不覆盖它）
        self.emitted = False

    def _extract(self, text: str) -> str:
        def repl(match):
            if self.on_doc is not None:
                title = match.group(1).strip()
                points = [p.strip() for p in re.split(r"[;；]", match.group(2)) if p.strip()]
                if title and points:
                    self.emitted = True
                    try:
                        self.on_doc({"title": title[:40], "points": points[:10]})
                    except Exception:
                        pass
            return ""

        return _DOC_RE.sub(repl, text)

    def feed(self, delta: str) -> str:
        """吃一块流式文本，返回可以安全播报的正文。"""
        self._buf += delta
        # ① 先摘掉已完整出现的标签
        self._buf = self._extract(self._buf)
        # ② 未闭合的 [DOC: ... 整段攒住，等后续块（半个标签也绝不能念出来）
        hold = self._buf.rfind(DOC_PREFIX)
        if hold < 0:
            # ③ 末尾若只是前缀（'['、'[D'、'[DO'、'[DOC'）也先攒住
            for n in range(min(len(DOC_PREFIX) - 1, len(self._buf)), 0, -1):
                tail = self._buf[-n:]
                if DOC_PREFIX.startswith(tail):
                    hold = len(self._buf) - n
                    break
        if hold >= 0:
            out, self._buf = self._buf[:hold], self._buf[hold:]
            return out
        out, self._buf = self._buf, ""
        return out

    def flush(self) -> str:
        """流结束：把残留（不完整的标签也当正文丢弃）吐出。"""
        rest = self._extract(self._buf)
        # 未闭合的 [DOC: ... 整段丢弃，绝不念给用户
        idx = rest.find("[DOC:")
        if idx >= 0:
            rest = rest[:idx]
        self._buf = ""
        return rest.rstrip()
