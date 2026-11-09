# -*- coding: utf-8 -*-
"""对话编排（搬运自 LiveTalking llm.py 的 llm_response，去掉 WebRTC 依赖改纯回调）。

用法：
    from llm import stream_dialogue, ConversationMemory

    memory = ConversationMemory()
    for event in stream_dialogue("你好", memory=memory):
        if event.type == "action":
            avatar.startSpeaking(first=True)      # greet
        elif event.type == "text":
            ui.appendText(event.text)             # 聊天气泡
        elif event.type == "segment":
            tts.speak(event.text)                 # 语音播报
        elif event.type == "module":
            voice_prompt_and_confirm(event.text)  # 模块推进

事件类型：
- action  动作标签（greet/idle/none）
- text    已放行的正文分片（可能含模块标签行，调用方自行过滤）
- segment 切好的可用语音段（标点边界，不含标签）
- module  模块推进意图（health/fatigue/emotion/companion）
- done    流结束（携带完整正文）
- error   LLM 调用失败（携带错误信息；正文为空）
"""

import re
import time
from dataclasses import dataclass, field

from .action_parser import ActionTagParser
from .config import LLM_CONFIG
from .llm_memory import ConversationMemory, strip_leading_tag
from .llm_service import LLMService
from .personas import build_system_prompt
from .text_slicer import SegmentSlicer

# 切句阈值（LiveTalking 实测值）：首段 8-24 字、后续 12-36 字，
# 弱标点首段 8 字、后续 14 字才切——保证韵律自然、不碎句。
FIRST_SEGMENT_MIN = 8
FIRST_SEGMENT_MAX = 24
SEGMENT_MIN = 12
SEGMENT_MAX = 36

# 问候语兜底：LLM 偶尔漏掉动作标签时，开场问候是唯一可确定性推断的场景
_GREETING_RE = re.compile(
    r"^\s*(你好|您好|嗨[吧呀~！？]?|哈喽|hello|hi[吧呀~！？]|在吗|早上好|中午好|下午好|晚上好|晚安|再见|拜拜)",
    re.IGNORECASE,
)


def _guess_greeting_action(message: str) -> str | None:
    if _GREETING_RE.match(message or ""):
        return "greet"
    return None


@dataclass
class DialogueEvent:
    type: str  # action / text / segment / module / done / error
    text: str = ""
    data: dict = field(default_factory=dict)


def stream_dialogue(
    message: str,
    *,
    memory: ConversationMemory | None = None,
    system_prompt: str | None = None,
    config: dict | None = None,
    should_stop=None,
):
    """流式跑一轮对话，yield DialogueEvent。"""
    started = time.perf_counter()
    parser = ActionTagParser()
    slicer = SegmentSlicer(
        FIRST_SEGMENT_MIN, FIRST_SEGMENT_MAX, FIRST_SEGMENT_MAX + 8,
        SEGMENT_MIN, SEGMENT_MAX, SEGMENT_MAX + 24,
        first_weak_min=FIRST_SEGMENT_MIN,
        weak_min=14,
    )
    stopped = should_stop or (lambda: False)
    collected: list[str] = []
    action_emitted = False

    def emit_action(tag: str | None):
        nonlocal action_emitted
        if action_emitted or not tag:
            return
        action_emitted = True
        yield DialogueEvent("action", tag)

    try:
        service = LLMService(config)
        prompt = system_prompt or build_system_prompt()
        stream = service.stream_text(message, system_prompt=prompt, memory=memory)
        first = True
        for delta in stream:
            if stopped():
                return
            if first:
                first = False
                yield DialogueEvent("text", "", {"first_chunk_latency": time.perf_counter() - started})
            body = parser.feed(delta)
            if body:
                # 动作标签在正文第一片到达时立即上报：数字人先开演再开口
                if parser.action:
                    yield from emit_action(parser.action)
                collected.append(body)
                yield DialogueEvent("text", body)
                for segment in slicer.feed(body):
                    yield DialogueEvent("segment", segment)
        tail = parser.flush()
        if tail:
            if parser.action:
                yield from emit_action(parser.action)
            collected.append(tail)
            yield DialogueEvent("text", tail)
            for segment in slicer.feed(tail):
                yield DialogueEvent("segment", segment)
        for segment in slicer.flush():
            yield DialogueEvent("segment", segment)
    except Exception as exc:  # LLM 不可达等：上报错误，正文留空
        yield DialogueEvent("error", str(exc))
        return

    # 兜底：LLM 漏标签且用户在明确问候时补 greet（LiveTalking 同款逻辑），
    # 保证 idle→greet→talk 的完整编排
    if not action_emitted and not parser.saw_tag:
        fallback = _guess_greeting_action(message)
        if fallback:
            yield DialogueEvent("action", fallback)
    # 模块推进意图
    if parser.module:
        yield DialogueEvent("module", parser.module)
    full = strip_leading_tag("".join(collected)).strip()
    yield DialogueEvent("done", full, {"action": parser.action, "module": parser.module})
