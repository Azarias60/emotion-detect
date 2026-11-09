# -*- coding: utf-8 -*-
"""直跳/确认跳/分诊（triage）单测（2026-10-25 plan.items[2]）。

覆盖四块（对应 2026-10-25 计划 items[2] 规格）：
  ① 分诊规则：点名直通可重触发 / 信息问句不触发 / R1 / R2 / 否定抑制 /
     平分躯体优先 / R3（白盒）/ EmotionTracker(role=guide) feed 出 kind=module；
  ② 语音路径（_handle_turn + FakeASR）：四模块直跳（module_jump；2026-10-25 起
     跳转不再清会话——会话跨页面继承，「你好」才是换访客信号）、
     问句两条不跳、FIX-2 回归两条（我去年体检血压偏高 / 过去问诊要排很久队）
     不跳且产出 transcript、FIX-1 回归（外放推荐语被麦克风收回的回声转写
     → ghost 抑制，绝不 module_jump）+ 对照（无近期回复时同句照跳）；
  ③ 确认跳：推荐存活「好/可以的」→ 跳、>120s 过期不跳、夹普通问话不清推荐；
  ④ 文字路径（_handle_text）：直跳一条 + 确认跳一条。

FIX-1 对应 microphone_stream.py _handle_turn 的顺序：ghost 判定 →
_try_direct_jump → _try_confirm_jump → 唤醒门（2026-10-25 前跳转判定在
ghost 之前，导览页外放推荐语被收回时页面会无人自跳）。
FIX-2 对应 _try_direct_jump 动词正则：删裸「去」，(?<!过)去(?=模块名) lookahead。
"""
from __future__ import annotations

import asyncio
import threading
import time
import unittest
from types import SimpleNamespace

import numpy as np

import server.microphone_stream as ms
from server.emotion_tracker import EmotionTracker
from server.microphone_stream import (
    GHOST_WINDOW_S,
    MicrophoneConnection,
    StreamingEnergyVAD,
)
from server.personas import load_personas
from server.triage import TriageScorer
from services.wake_word import WakeWordGate


load_personas()


# ── 最小假件 ────────────────────────────────────────────────────────────
class FakeWS:
    async def send_bytes(self, payload):
        pass

    async def send_json(self, payload):
        pass


class FakeASR:
    """返回固定转写的假 ASR（ResidentWhisper.transcribe_pcm 同签名）。"""

    def __init__(self, text: str) -> None:
        self.text = text

    def transcribe_pcm(self, audio) -> str:
        return self.text


class FakeMemory:
    def __init__(self) -> None:
        self.cleared = 0

    def clear(self) -> None:
        self.cleared += 1


class FakeTTS:
    tag = "fake"

    def __init__(self) -> None:
        self.texts: list[str] = []

    def _generate(self, text, event=None):
        self.texts.append(text)
        yield np.full(512, 0.1, dtype="float32")


class CapLLM:
    """替换 ms.LLMService：不外呼，吐固定三段。"""

    def __init__(self, opt) -> None:
        pass

    def stream_text(self, message, system_prompt=None, memory=None):
        for chunk in ("好的", "，我来", "介绍一下。"):
            yield chunk


def _make_conn(asr_text: str, *, mode: str = "default", persona: str = "guide") -> MicrophoneConnection:
    """经 __new__ 构造最小连接（跑真实 _handle_turn/_handle_text 逻辑）。"""
    conn = MicrophoneConnection.__new__(MicrophoneConnection)
    conn.ws = FakeWS()
    conn.opt = SimpleNamespace(default_tts="cosyvoice3", mic_reopen_grace_ms=0)
    conn.asr = FakeASR(asr_text)
    conn.mode = mode
    conn.session_id = ""
    conn.generation = 0
    conn.live_reply_id = 0
    conn.closed = threading.Event()
    conn.loop = asyncio.get_running_loop()
    conn.output = asyncio.Queue(maxsize=400)
    conn.reply_text = ""
    conn.last_reply_audio_at = None
    conn.persona_override = persona
    conn._last_rec = None
    conn._emotion_note = ""
    conn._last_pcm_at = None
    conn.carry_audio = None
    conn.pending_turn = None
    conn.pending_task = None
    conn.vad = StreamingEnergyVAD()
    conn.emotions = EmotionTracker(role="guide" if persona == "guide" else "companion")
    conn.llm_memory = FakeMemory()
    conn.wake = WakeWordGate(word_default="你好", word_live="")
    conn._tts = FakeTTS()
    conn._make_default_tts = lambda: conn._tts
    return conn


def _drain(conn) -> list[dict]:
    events = []
    while not conn.output.empty():
        kind, payload, _generation = conn.output.get_nowait()
        if kind == "json" and isinstance(payload, dict):
            events.append(payload)
    return events


def _kinds(events) -> list[str]:
    return [e.get("type") for e in events]


def _jumps(events) -> list[str]:
    return [e.get("module") for e in events if e.get("type") == "module_jump"]


async def _run_turn(conn, text: str) -> list[dict]:
    await conn._handle_turn(np.zeros(1600, "<i2"), 0, conn.mode, "")
    return _drain(conn)


# ── ① 分诊规则 ──────────────────────────────────────────────────────────
class TriageRuleTests(unittest.TestCase):
    def test_module_name_direct_pass_is_retriggerable(self):
        """点名直通：说模块名即推荐，且不受每模块一次限制（再次点名刷新）。"""
        s = TriageScorer()
        first = s.feed("我想做健康问诊")
        self.assertIsNotNone(first)
        self.assertEqual("health", first["module"])
        self.assertEqual("module", first["kind"])
        self.assertIn("点名", first["rule"])
        second = s.feed("我想做健康问诊")
        self.assertIsNotNone(second, "第二次点名应可重触发")
        self.assertEqual("health", second["module"])

    def test_info_question_does_not_trigger(self):
        """信息问句不触发（"检测"是直接请求词，但问句形态要抑制掉）。"""
        s = TriageScorer()
        self.assertIsNone(s.feed("疲劳检测是什么"))
        self.assertIsNone(s.feed("情绪识别怎么用"))
        self.assertIsNone(s.feed("健康检测什么意思"))
        # 问句也不该吞掉后续正常推荐的资格（fired 未被污染）
        rec = s.feed("我最近头疼，还有点恶心")
        self.assertEqual("health", rec["module"])

    def test_r1_immediate_strong_signal(self):
        """R1：单句双强信号（头疼+恶心=4 分）即时推荐躯体维度。"""
        s = TriageScorer()
        rec = s.feed("我最近头疼，还有点恶心")
        self.assertIsNotNone(rec)
        self.assertEqual("health", rec["module"])
        self.assertTrue(rec["rule"].startswith("R1"), rec["rule"])

    def test_r2_threshold_cross(self):
        """R2：单句普通信号词（累=1 分）即过线（CONSECUTIVE=1.0）。"""
        s = TriageScorer()
        rec = s.feed("我最近好累")
        self.assertIsNotNone(rec)
        self.assertEqual("fatigue", rec["module"])
        self.assertTrue(rec["rule"].startswith("R2"), rec["rule"])

    def test_negation_suppresses_signal(self):
        """否定抑制：命中词前 3 字内不/没 → 计 0 分但仍记录。"""
        s = TriageScorer()
        rec = s.feed("不怎么累，就是心情不好")
        self.assertIsNotNone(rec)
        self.assertEqual("emotion", rec["module"], "累被否定后应走情绪链路")
        plain = TriageScorer()
        self.assertIsNone(plain.feed("我不头疼"), "否定后的唯一信号词不应触发推荐")

    def test_tie_prefers_somatic(self):
        """平分裁决：三维同分时 躯体 > 疲劳 > 情绪。"""
        s = TriageScorer()
        rec = s.feed("我不舒服，好累，有点烦")
        self.assertIsNotNone(rec)
        self.assertEqual("health", rec["module"], "health/fatigue/emotion 同为 1 分应躯体优先")

    def test_r3_accumulate_branch(self):
        """R3 分支（白盒）：CONSECUTIVE=1.0 下正常路径先被 R2 拦住，直接预置
        累计分验证分支本体仍生效（供未来调参），阈值 4.5×衰减 0.65 → 预置 7.0。"""
        s = TriageScorer()
        s.scores["emotion"] = 7.0
        rec = s.feed("你好呀")  # 本句无任何信号词，u=0
        self.assertIsNotNone(rec)
        self.assertEqual("emotion", rec["module"])
        self.assertTrue(rec["rule"].startswith("R3"), rec["rule"])


class EmotionTrackerTriageTests(unittest.TestCase):
    def test_guide_feed_emits_module_recommend(self):
        """guide 角色把分诊结果转成 kind=module 的推荐事件。"""
        tracker = EmotionTracker(role="guide")
        events = tracker.feed("我最近头疼，还有点恶心")
        kinds = [e.get("type") for e in events]
        self.assertIn("emotion", kinds)
        recs = [e for e in events if e.get("kind") == "module"]
        self.assertEqual(1, len(recs), events)
        self.assertEqual("recommend", recs[0]["type"])
        self.assertEqual("health", recs[0]["module"])

    def test_companion_feed_has_no_module_recommend(self):
        """companion 角色不做模块推荐（角色接线回归）。"""
        tracker = EmotionTracker(role="companion")
        events = tracker.feed("我最近头疼，还有点恶心")
        self.assertFalse([e for e in events if e.get("kind") == "module"], events)


# ── ② 语音路径直跳 / 问句 / FIX-1 / FIX-2 ──────────────────────────────
class VoiceTurnJumpTests(unittest.IsolatedAsyncioTestCase):
    async def test_voice_direct_jump_four_modules(self):
        """语音直跳四模块：module_jump 且 module 正确；跳转不清会话（2026-10-25）。"""
        cases = [
            ("跳转到健康问诊", "health"),
            ("我想做疲劳检测", "fatigue"),
            ("打开情绪识别", "emotion"),
            ("跳转到情感数字人", "companion"),
        ]
        for phrase, module in cases:
            with self.subTest(phrase=phrase):
                conn = _make_conn(phrase)
                conn.wake.open("default")  # 已唤醒会话
                events = await _run_turn(conn, phrase)
                self.assertEqual([module], _jumps(events), _kinds(events))
                self.assertNotIn("session_reset", _kinds(events))
                self.assertNotIn("transcript", _kinds(events), "跳转不该起说话链路")

    async def test_voice_question_does_not_jump(self):
        """问句两条不跳：转写照常产出、走 LLM 链路。"""
        for phrase in ("疲劳检测是什么", "情绪识别怎么做"):
            with self.subTest(phrase=phrase):
                conn = _make_conn(phrase)
                conn.wake.open("default")
                orig = ms.LLMService
                ms.LLMService = CapLLM
                try:
                    events = await _run_turn(conn, phrase)
                finally:
                    ms.LLMService = orig
                self.assertEqual([], _jumps(events), _kinds(events))
                transcripts = [e for e in events if e.get("type") == "transcript"]
                self.assertTrue(transcripts, _kinds(events))
                self.assertIn("response_done", _kinds(events))

    async def test_fix2_history_narration_does_not_jump(self):
        """FIX-2 回归：叙述历史句（去年/过去+裸去场景）不再误跳。"""
        for phrase in ("我去年体检血压偏高", "过去问诊要排很久队"):
            with self.subTest(phrase=phrase):
                conn = _make_conn(phrase)
                conn.wake.open("default")
                orig = ms.LLMService
                ms.LLMService = CapLLM
                try:
                    events = await _run_turn(conn, phrase)
                finally:
                    ms.LLMService = orig
                self.assertEqual([], _jumps(events), _kinds(events))
                transcripts = [e for e in events if e.get("type") == "transcript"]
                self.assertTrue(transcripts, _kinds(events))
                self.assertEqual(phrase, transcripts[0].get("text"))

    async def test_fix1_echo_of_recommend_reply_suppressed_no_jump(self):
        """FIX-1 回归：推荐语外放被收回（reply_text 命中 + 刚出过声）→
        ghost 抑制必须先于跳转判定：无 module_jump、无 transcript（静默丢弃）。"""
        echo = "建议做一次疲劳检测，要做就说声好"
        conn = _make_conn(echo)
        conn.wake.open("default")
        conn.reply_text = echo
        conn.last_reply_audio_at = time.monotonic()  # 刚说完话（< GHOST_WINDOW_S）
        self.assertLess(0.0, GHOST_WINDOW_S)
        events = await _run_turn(conn, echo)
        self.assertEqual([], _jumps(events), f"回声转写不该触发跳转: {_kinds(events)}")
        self.assertNotIn("transcript", _kinds(events), _kinds(events))
        self.assertNotIn("response_done", _kinds(events))

    async def test_fix1_control_real_speech_still_jumps(self):
        """FIX-1 对照：同一句、但没有近期回复（reply_text 空）=真人新话轮，
        ghost 放行 → 直跳照常，证明抑制只吃回声不吃真人。"""
        phrase = "建议做一次疲劳检测，要做就说声好"
        conn = _make_conn(phrase)
        conn.wake.open("default")
        self.assertIsNone(conn.last_reply_audio_at)  # 本次连接还没出过声
        events = await _run_turn(conn, phrase)
        self.assertEqual(["fatigue"], _jumps(events), _kinds(events))
        self.assertNotIn("session_reset", _kinds(events))

    async def test_short_confirm_word_survives_noise_filter(self):
        """噪声筛选不得误杀单字确认词「好」（2026-10-26 实测回归：
        上一次确认跳转清掉 _last_rec 后，紧跟着的「好。」被当环境音丢弃）。"""
        conn = _make_conn("好。")
        conn.wake.open("default")
        conn._last_rec = None  # 已被上一次确认清掉的形态
        events = await _run_turn(conn, "好。")
        self.assertFalse(
            any(e.get("quiet") for e in events),
            f"「好。」被噪声筛选误杀: {_kinds(events)}",
        )

    async def test_misheard_verb_variant_direct_jump(self):
        """ASR 误听动词变体（跳转→调转）也要直跳（2026-10-26 实测
        "调转到情感数字人。"没跳，掉进普通聊天）。"""
        conn = _make_conn("调转到情感数字人。")
        conn.wake.open("default")
        events = await _run_turn(conn, "调转到情感数字人。")
        self.assertEqual(["companion"], _jumps(events), _kinds(events))

    async def test_llm_text_recommend_emits_recommend_event(self):
        """LLM 文本里主动推荐模块 → 必须发 recommend 事件同步前端推荐卡
        （2026-10-26 用户反馈"推荐和资料卡不同步"）并登记待确认。"""
        conn = _make_conn("")
        await asyncio.to_thread(
            conn._stream_default_reply,
            "",
            0,
            canned_reply="好的，情感数字人可以陪您聊聊天，要做就说声好哦。",
        )
        events = _drain(conn)
        recs = [e for e in events if e.get("type") == "recommend"]
        self.assertEqual(1, len(recs), _kinds(events))
        self.assertEqual("companion", recs[0].get("module"))
        self.assertEqual("module", recs[0].get("kind"))
        self.assertTrue(recs[0].get("reason"))
        self.assertIsNotNone(conn._last_rec)
        self.assertEqual("companion", conn._last_rec["module"])


# ── ③ 确认跳 ────────────────────────────────────────────────────────────
class ConfirmJumpTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _rec(age_s: float = 0.0) -> dict:
        return {
            "module": "fatigue",
            "reason": "建议做一次疲劳检测，看看疲劳程度？",
            "at": time.monotonic() - age_s,
            "turns": 0,
        }

    async def test_accept_phrase_confirms_recommend(self):
        """推荐存活期内说「好/可以的」→ 跳对应模块并清推荐。"""
        for phrase in ("好", "可以的"):
            with self.subTest(phrase=phrase):
                conn = _make_conn(phrase)
                conn._last_rec = self._rec()
                jumped = await conn._try_confirm_jump(phrase)
                events = _drain(conn)
                self.assertTrue(jumped, _kinds(events))
                self.assertEqual(["fatigue"], _jumps(events))
                self.assertNotIn("session_reset", _kinds(events))
                self.assertIsNone(conn._last_rec)

    async def test_expired_recommend_does_not_jump(self):
        """推荐超过 180s / 4 轮 → 过期不跳、清掉待确认状态。"""
        conn = _make_conn("好")
        conn._last_rec = self._rec(age_s=181.0)
        jumped = await conn._try_confirm_jump("好")
        self.assertFalse(jumped)
        self.assertEqual([], _jumps(_drain(conn)))
        self.assertIsNone(conn._last_rec, "过期推荐应被清空")

    async def test_intervening_question_keeps_recommend(self):
        """推荐→确认之间插一句普通问话：不清推荐（3 轮/120s 内仍可确认）。"""
        conn = _make_conn("疲劳检测是什么")
        conn._last_rec = self._rec()
        jumped = await conn._try_confirm_jump("疲劳检测是什么")
        self.assertFalse(jumped)
        self.assertEqual([], _jumps(_drain(conn)))
        self.assertIsNotNone(conn._last_rec, "非确认话轮不应清掉待确认推荐")
        self.assertEqual(1, conn._last_rec["turns"])
        # 随后的确认词仍能跳
        jumped = await conn._try_confirm_jump("好")
        self.assertTrue(jumped)
        self.assertEqual(["fatigue"], _jumps(_drain(conn)))


# ── ④ 文字路径 ──────────────────────────────────────────────────────────
class TextPathJumpTests(unittest.IsolatedAsyncioTestCase):
    async def test_text_direct_jump(self):
        """文字直跳：唤醒门未开（打字绕过门）也能直接导航。"""
        conn = _make_conn("")  # asr 不参与文字路径
        # 唤醒门保持未唤醒（required=True）→ 裸问候分支跳过 → 直跳生效
        events = []
        await conn._handle_text({"type": "text", "text": "打开情绪识别"})
        events = _drain(conn)
        self.assertEqual(["emotion"], _jumps(events), _kinds(events))
        self.assertNotIn("session_reset", _kinds(events))

    async def test_text_confirm_jump(self):
        """文字确认跳：待确认推荐存在时打字「好的」→ 跳。"""
        conn = _make_conn("")
        conn._last_rec = {
            "module": "fatigue",
            "reason": "建议做一次疲劳检测，看看疲劳程度？",
            "at": time.monotonic(),
            "turns": 0,
        }
        await conn._handle_text({"type": "text", "text": "好的"})
        events = _drain(conn)
        self.assertEqual(["fatigue"], _jumps(events), _kinds(events))
        self.assertNotIn("session_reset", _kinds(events))
        self.assertIsNone(conn._last_rec)


if __name__ == "__main__":
    unittest.main()
