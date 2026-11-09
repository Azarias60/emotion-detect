"""Streaming LLM adapter for browser and legacy HTTP sessions."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from services.action_parser import (
    ACTION_PROMPT_RULES,
    ActionTagParser,
    guess_greeting_action,
    state_for_action,
)
from services.doc_parser import DOC_PROMPT_RULES, DocTagStripper
from services.llm_service import LLMService
from services.text_slicer import SegmentSlicer
from server.personas import active_system_prompt, get_persona
from utils.logger import logger

if TYPE_CHECKING:
    from avatars.base_avatar import BaseAvatar


SENTENCE_ENDINGS = set(",.!?;:，。！？；：")

# 切句阈值：首段越早送 TTS，首音延迟越低；后续段在播放期间并行合成，
# 维持较大的段落以保证韵律自然。首段下限 8 与固定音色注册文本（22 字）
# 保持 ≥ 一半比例，过短会诱发 zero-shot 续写幻觉（HANDOFF_FIXES.md P0-B）。
# 6（2026-10-24 压语音响应）：首句越短 TTS 出第一块音频越快（实测 3 字
# 0.37s / 11 字 1.0-1.6s）；8→6 让 TTS 提前约 0.3-0.5s 出声。guide_boy_v1
# 注册样本 11.8s，短段比例安全（旧 8 是为 22 字固定音色样本定的）。
FIRST_SEGMENT_MIN = 6
FIRST_SEGMENT_MAX = 24
# 后续段 12 字起步：太短（6~8 字）会反复付 TTS 首包开销、句子碎；
# 太长（20+）会拖慢第二句开口，攒段等待还会把硬上限切到词中间。
SEGMENT_MIN = 12
SEGMENT_MAX = 36


def trigger_avatar_action(avatar_session: "BaseAvatar", tag: str | None) -> str:
    """Drive the choreography state machine from an LLM action tag.

    返回四态之一：

    - ``"played"``   动作已开演；
    - ``"skipped"``  标签已消费，但该形象的段表没有这个动作（例如 yy 只有
                     comfort），静默降级为"本条回复不播动作"；
    - ``"none"``     本来就没有可执行的动作标签（idle/none/漏标签）；
    - ``"failed"``   调用出错，调用方可以重试。

    ``skipped`` 必须区别于 ``failed``：段表里没有的动作是**正常情况**，
    每次都当失败重试会在一轮回复里刷几十条异常堆栈（实测 22 条）。
    ``none`` 必须区别于 ``skipped``：漏标签时要留给问候兜底继续判断。
    """
    state = state_for_action(tag)
    if state is None:
        return "none"
    setter = getattr(avatar_session, "set_custom_state", None)
    if setter is None:
        return "failed"
    try:
        result = setter(state)
    except Exception:
        logger.exception("[Action] 触发动作 %r 失败", tag)
        return "failed"
    if result is False:
        # 段表没有这个动作段：标签照常消费，静默降级为"不播动作"
        logger.info(
            "[Action] 当前形象没有动作段 %r（%s），本条回复不播动作",
            tag,
            state,
        )
        return "skipped"
    logger.info("[Action] LLM 语义动作 %r -> 状态段 %s", tag, state)
    return "played"


def _action_consumed(result: str) -> bool:
    """标签是否已被处理（played/skipped 都算，避免重复触发）。"""
    return result in ("played", "skipped")


# 导览页"值得进资料卡"的内容门（2026-10-23 用户拍板：日常不动资料栏，
# 观点/解释才切换）。只用强信号——观点标记（建议/适合…）与解释标记
# （舌象/眨眼频率/是通过…）。模块名本身不算：导览员日常反问"想做哪个检测"
# 也会提到模块名，那属闲聊（实测踩过：用宽词表会把"你好呀"也提炼成资料卡）。
GUIDE_ADVICE_WORDS = ("建议", "适合", "合适", "推荐", "最好", "不宜", "值得", "应该", "不妨")
GUIDE_EXPLAIN_WORDS = (
    "舌象", "舌苔", "眨眼", "哈欠", "心率", "血氧", "面色", "静坐", "普通话",
    "是通过", "指的是", "主要看",
)


def _has_guide_content(text: str) -> bool:
    """导览页回复是否含实质内容（决定要不要自动提炼进资料卡）。

    只靠标记表区分——标记表已收紧（模块泛词如"检测/评估/情绪"不计），
    导览员的反问式闲聊天然不含标记。不再一票否决问号结尾：暖暖给完建议
    常跟一个追问（"…挺合适的。你最近是不是有这类感觉？"），那是有实质
    内容的（实测踩过：问号 guard 会把这类全拦掉）。
    """
    t = (text or "").strip()
    if not t:
        return False
    return any(w in t for w in GUIDE_ADVICE_WORDS) or any(w in t for w in GUIDE_EXPLAIN_WORDS)


def _auto_points(text: str) -> list[str]:
    """把一段回答按句切成资料要点；不足 2 句且不足 40 字返回空（视为寒暄）。"""
    import re as _re

    cleaned = (text or "").strip()
    if not cleaned:
        return []
    parts = [p.strip() for p in _re.split(r"[。！？!?\n]+", cleaned) if p and len(p.strip()) >= 4]
    if not parts:
        return []
    if len(parts) < 2 and len(cleaned) < 40:
        return []
    # 合并过碎的分句（顿号/逗号开头的残句并入前一条），最多 10 条
    merged: list[str] = []
    for part in parts:
        if merged and (part[0] in "，、；：,;:" or len(part) < 6):
            merged[-1] = merged[-1] + part
        else:
            merged.append(part)
    return merged[:10]


def _run_canned_reply(
    text: str,
    on_text,
    on_doc,
    avatar_session,
    event: dict,
    persona_override: str,
    auto_doc: bool,
) -> None:
    """预置回复：不调 LLM、不写会话记忆（一次性播报，如结果回读/推荐询问）。

    仍走资料标签剥离与 [DOC:] 事件：预置内容也可以带教学要点，
    页面资料卡行为与正常回复完全一致。
    """
    parser = ActionTagParser()
    parser.feed(text)
    body = parser.flush()

    doc_stripper = DocTagStripper(on_doc) if on_doc is not None else None
    spoken = body
    if doc_stripper is not None:
        spoken = doc_stripper.feed(body)
        spoken += doc_stripper.flush()
    if on_text is not None and spoken:
        try:
            on_text(spoken)
        except Exception:
            logger.exception("[Reply] on_text 回调失败（预置回复）")
    for segment in (spoken or "").split("，"):
        if segment:
            avatar_session.put_msg_txt(segment + "，", event)


def llm_response(
    message: str,
    avatar_session: "BaseAvatar",
    datainfo: dict | None = None,
    on_action=None,
    on_text=None,
    should_stop=None,
    on_doc=None,
    context_note: str = "",
    persona_override: str = "",
    auto_doc: bool = True,
    canned_reply: str = "",
) -> None:
    """Stream an LLM reply into TTS using the session's configured provider.

    A leading action tag (``[comfort]`` …) is parsed from the stream, drives
    the avatar action segment and never reaches TTS. ``on_action(tag)`` is
    invoked (from this thread) when a semantic action fires, so callers can
    surface it in the live UI.

    ``on_text(body)`` receives every speakable body handed to TTS (the live
    page uses it to track what the avatar is saying, for echo suppression).
    ``should_stop()`` is polled between chunks: when it turns true the reply
    stops feeding TTS immediately, so a superseded reply cannot keep piling
    segments on top of the new one.

    The reply token (``begin_reply()``) is captured **once, at reply start**,
    and stamped on every segment. That is what makes ``flush_talk()``
    (barge-in / new user message) drop this reply's late segments instead of
    replaying them alongside the new reply.
    """

    started = time.perf_counter()
    parser = ActionTagParser()
    # 强标点（。！？）攒够 8 字即切——句末是天然段界；弱标点（，、）
    # 首段攒够 8 字、后续攒够 14 字才切，否则每个逗号都会把回复切碎
    slicer = SegmentSlicer(
        FIRST_SEGMENT_MIN, FIRST_SEGMENT_MAX, FIRST_SEGMENT_MAX + 8,
        SEGMENT_MIN, SEGMENT_MAX, SEGMENT_MAX + 24,
        first_weak_min=FIRST_SEGMENT_MIN,
        weak_min=14,
    )
    action_emitted = False
    fallback_done = False
    first = True
    event = dict(datainfo or {})
    # 导览页（persona=guide）的语音用暖暖的云霞卡通音色（guide_boy_v1），
    # 与陪伴页的训练音色按回复级区分，两页互不影响（2026-10-23/24 用户拍板）
    if persona_override == "guide":
        event["voice"] = "boy"
    # 代际令牌在回复开始时锁定；之后被打断也不会变，于是本回复的段落
    # 全部带旧代际，被 TTS 的过期检查丢弃。
    begin_reply = getattr(avatar_session, "begin_reply", None)
    if begin_reply is not None:
        try:
            event["reply_epoch"] = begin_reply()
        except Exception:
            logger.exception("[Reply] 取回复代际失败，本次不做代际标记")
    stopped = should_stop or (lambda: False)
    text_stream = None
    # 会话压缩：历史轮数过多时先折叠最早几轮为一条摘要（防上下文无限增长）
    memory_obj = getattr(avatar_session, "llm_memory", None)
    if memory_obj is not None and hasattr(memory_obj, "compress"):
        try:
            memory_obj.compress()
        except Exception:
            logger.exception("[Memory] 会话压缩失败（不影响本轮）")
    try:
        if canned_reply:
            _run_canned_reply(
                canned_reply, on_text, on_doc, avatar_session, event,
                persona_override, auto_doc,
            )
            return
        service = LLMService(avatar_session.opt)
        # 人设：连接级覆盖（导览页 configure 带 persona=guide）优先，
        # 否则用 live 模式当前人设（personas/*.md 里 mode: live）
        override = get_persona(persona_override) if persona_override else None
        base_prompt = (
            override.prompt
            if override is not None and override.prompt
            else active_system_prompt(
                mode="live",
                fallback=getattr(avatar_session.opt, "llm_system_prompt", None),
            )
        )
        system_prompt = (
            base_prompt
            + ACTION_PROMPT_RULES
            + DOC_PROMPT_RULES
            # 情绪上下文：把"用户此刻偏什么情绪、连续说了几句"告诉模型，
            # 让它调整语气与建议（没有情绪信息时为空串，行为不变）
            + (("\n" + context_note.strip()) if context_note and context_note.strip() else "")
        )

        def _emit_doc(doc: dict) -> None:
            if on_doc is not None:
                try:
                    on_doc(doc)
                except Exception:
                    logger.exception("[Doc] on_doc 回调失败")

        doc_stripper = DocTagStripper(_emit_doc)

        # 已送 TTS 的全文（不含资料标签）：供回答完成后自动要点化进资料框
        spoken: list[str] = []

        def speak(body: str) -> None:
            # 资料标签绝不能被念出来：先过剥离器（保留最多 5 字符，无首字延迟）
            body = doc_stripper.feed(body)
            if not body:
                return
            spoken.append(body)
            if on_text is not None:
                # 累计"已交给 TTS 的正文"：live 页靠它做回声（ghost）判定，
                # 必须覆盖整段正文，而不只是刚好切出段落的那部分
                try:
                    on_text(body)
                except Exception:
                    logger.exception("[Reply] on_text 回调失败")
            # 软边界切句：优先在标点处切段；字数超上限后继续等标点，
            # 只有整段无标点才在硬上限兜底切断（避免词被劈开）
            for segment in slicer.feed(body):
                avatar_session.put_msg_txt(segment, event)

        text_stream = service.stream_text(
            message,
            system_prompt=system_prompt,
            memory=getattr(avatar_session, "llm_memory", None),
        )
        for delta in text_stream:
            if stopped():
                logger.info(
                    "[Reply] 本轮回复已被新一轮取代，停止供段（%.2fs）",
                    time.perf_counter() - started,
                )
                return
            if first:
                logger.info(
                    "LLM first chunk latency: %.3fs",
                    time.perf_counter() - started,
                )
                first = False
            body = parser.feed(delta)
            if body and not action_emitted:
                result = trigger_avatar_action(avatar_session, parser.action)
                action_emitted = _action_consumed(result)
                if result == "played" and on_action is not None:
                    try:
                        on_action(parser.action)
                    except Exception:
                        logger.exception("[Action] on_action 回调失败")
            # 兜底：开头一判定不是标签且用户在明确问候，立即补 greet，
            # 赶在语音开始之前，保证 idle→greet→talk 的完整编排
            if (
                not action_emitted
                and not parser.saw_tag
                and parser.done
                and not fallback_done
            ):
                fallback_done = True
                fallback_tag = guess_greeting_action(message)
                if fallback_tag:
                    result = trigger_avatar_action(avatar_session, fallback_tag)
                    action_emitted = _action_consumed(result)
                    if result == "played" and on_action is not None:
                        try:
                            on_action(fallback_tag)
                        except Exception:
                            logger.exception("[Action] on_action 回调失败")
            speak(body)
        if stopped():
            return
        speak(parser.flush())
        speak(doc_stripper.flush())

        # 自动资料框：LLM 没主动写 [DOC:] 时，把回答本身按句切成要点推送。
        # 短寒暄（不足 2 句且不足 40 字）不推——资料框只承接"值得看第二眼"的回答。
        # 导览页额外过内容词门：只有涉及检测/健康/观点建议的实质内容才提炼，
        # 日常寒暄与情绪闲聊不动资料栏（2026-10-23 用户拍板）。
        if on_doc is not None and auto_doc and not doc_stripper.emitted:
            full_text = "".join(spoken).strip()
            if persona_override == "guide" and not _has_guide_content(full_text):
                full_text = ""
            points = _auto_points(full_text) if full_text else []
            if points:
                title = f"关于「{(message or '')[:14]}」" if (message or "").strip() else "数字人回答"
                try:
                    on_doc({"title": title, "points": points, "auto": True})
                except Exception:
                    logger.exception("[Doc] 自动资料推送失败（不影响对话）")
        tail = slicer.flush()
        if tail:
            # 尾巴只送 TTS：speak(parser.flush()) 已把整段正文（含这段尾巴）交给
            # on_text，这里再回传一次会让页面上的最后一句出现两遍
            avatar_session.put_msg_txt(tail, event)
        logger.info(
            "LLM stream completed in %.2fs", time.perf_counter() - started
        )
    except Exception:
        logger.exception("LLM streaming failed")
    finally:
        close = getattr(text_stream, "close", None)
        if close is not None:
            close()
