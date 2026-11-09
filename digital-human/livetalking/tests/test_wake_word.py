"""唤醒词门控：匹配/变体/拆词/重置/禁用/分模式 + 麦克风轮集成。"""

from __future__ import annotations

import asyncio
import threading
import unittest
from types import SimpleNamespace

import numpy as np

from services.wake_word import WakeWordGate


class WakeWordGateTests(unittest.TestCase):
    def test_blocks_until_word_heard_then_strips_it(self):
        gate = WakeWordGate("暖暖", "你好")
        proceed, message, woke = gate.check("default", "今天天气不错")
        self.assertFalse(proceed)
        self.assertFalse(woke)
        proceed, message, woke = gate.check("default", "暖暖，我今天考砸了")
        self.assertTrue(proceed)
        self.assertTrue(woke)
        self.assertEqual("我今天考砸了", message)
        # 唤醒后直接放行
        proceed, message, woke = gate.check("default", "继续聊聊")
        self.assertTrue(proceed)
        self.assertEqual("继续聊聊", message)
        self.assertFalse(woke)

    def test_bare_wake_word_becomes_greeting(self):
        gate = WakeWordGate("暖暖", "你好")
        proceed, message, _ = gate.check("default", "暖暖")
        self.assertTrue(proceed)
        self.assertEqual("你好", message)
        proceed, message, _ = gate.check("default", "暖暖！")
        self.assertTrue(proceed)

    def test_live_mode_word_variants_and_independence(self):
        gate = WakeWordGate("暖暖", "你好")
        # live 模式未唤醒，default 的词不通用
        self.assertFalse(gate.check("live", "暖暖在吗")[0])
        proceed, message, woke = gate.check("live", "您好，介绍一下自己")
        self.assertTrue(proceed)
        self.assertTrue(woke)
        self.assertEqual("介绍一下自己", message)
        # default 模式仍处待命
        self.assertTrue(gate.required("default"))
        self.assertFalse(gate.required("live"))

    def test_mid_sentence_word_and_spaces(self):
        gate = WakeWordGate("暖暖", "你好")
        proceed, message, _ = gate.check("default", "那个 暖暖 你在听吗")
        self.assertTrue(proceed)
        self.assertEqual("你在听吗", message)

    def test_reset_rearms_mode(self):
        gate = WakeWordGate("暖暖", "你好")
        gate.check("default", "暖暖你好")
        self.assertFalse(gate.required("default"))
        gate.reset("default")
        self.assertTrue(gate.required("default"))
        gate.check("live", "你好")
        gate.reset()
        self.assertTrue(gate.required("live"))
        self.assertTrue(gate.required("default"))

    def test_empty_word_disables_gate(self):
        gate = WakeWordGate("", "")
        self.assertFalse(gate.required("default"))
        proceed, message, woke = gate.check("default", "直接说话")
        self.assertTrue(proceed)
        self.assertFalse(woke)
        self.assertEqual("直接说话", message)


class FakeASR:
    def __init__(self, text: str):
        self.text = text

    def transcribe_pcm(self, pcm):
        return self.text


class FakeWS:
    async def send_bytes(self, payload):
        pass

    async def send_json(self, payload):
        pass


class MicrophoneWakeIntegrationTests(unittest.IsolatedAsyncioTestCase):
    def _make_conn(self, transcript: str):
        from server.microphone_stream import MicrophoneConnection
        from server.personas import load_personas

        load_personas()  # 热词校正需要注册表
        opt = SimpleNamespace(
            default_tts="cosyvoice3",
            default_tts_voice="test",
            mic_reopen_grace_ms=0,
            wake_word_default="暖暖",
            wake_word_live="你好",
        )
        conn = MicrophoneConnection.__new__(MicrophoneConnection)
        conn.ws = FakeWS()
        conn.opt = opt
        conn.asr = FakeASR(transcript)
        conn.mode = "default"
        conn.session_id = ""
        conn.generation = 0
        conn.closed = threading.Event()
        conn.loop = asyncio.get_running_loop()
        conn.output = asyncio.Queue(maxsize=64)
        conn.reply_text = ""
        conn.last_reply_audio_at = None
        conn.pending_turn = None
        conn.pending_task = None
        conn.wake = WakeWordGate("暖暖", "你好")
        # __init__ 里会设、_handle_turn 路径会读的连接级属性（绕过 __init__
        # 构造的夹具必须补齐，否则属性缺失把整轮打断成 error 事件）
        conn.persona_override = ""
        conn._last_rec = None
        conn._emotion_note = ""
        conn.llm_memory = None
        from server.emotion_tracker import EmotionTracker

        conn.emotions = EmotionTracker(role="companion")
        conn.live_reply_id = 0
        conn.replies = []
        conn._stream_default_reply = (
            lambda text, gen, canned_reply="": conn.replies.append(text)
        )
        # _reset_session 路径（「你好」软重置等）会读的属性
        conn.carry_audio = None
        conn._last_pcm_at = None
        conn.vad = SimpleNamespace(reset=lambda: None)
        conn._tasks = set()
        return conn

    async def _run_turn(self, conn):
        await conn._handle_turn(
            np.zeros(16000, dtype=np.int16), 0, "default", ""
        )

    async def _events(self, conn):
        out = []
        while not conn.output.empty():
            out.append(conn.output.get_nowait()[1])
        return out

    async def test_turn_without_wake_word_is_dropped(self):
        conn = self._make_conn("今天天气不错")
        await self._run_turn(conn)
        events = await self._events(conn)
        kinds = [e.get("type") for e in events]
        self.assertIn("wake_word_pending", kinds)
        self.assertNotIn("transcript", kinds)
        # ASR 阶段指示正常存在，但不应进入 LLM
        llm_stages = [
            e for e in events
            if e.get("type") == "pipeline" and e.get("stage") == "llm"
        ]
        self.assertEqual([], llm_stages)
        self.assertEqual([], conn.replies)

    async def test_turn_with_wake_word_proceeds_and_strips(self):
        conn = self._make_conn("暖暖，我今天考砸了")
        await self._run_turn(conn)
        events = await self._events(conn)
        kinds = [e.get("type") for e in events]
        self.assertIn("wake", kinds)
        self.assertIn("transcript", kinds)
        self.assertIn("pipeline", kinds)
        transcript = next(e for e in events if e.get("type") == "transcript")
        self.assertEqual("我今天考砸了", transcript["text"])
        self.assertEqual(["我今天考砸了"], conn.replies)

    async def test_hotword_mishearing_still_wakes(self):
        # 换换=暖暖 热词：Whisper 听错也能唤醒
        conn = self._make_conn("换换，我有点难过")
        await self._run_turn(conn)
        events = await self._events(conn)
        kinds = [e.get("type") for e in events]
        self.assertIn("wake", kinds)
        self.assertIn("transcript", kinds)
        self.assertEqual(["我有点难过"], conn.replies)


class HelloNewUserResetTests(unittest.IsolatedAsyncioTestCase):
    """「你好」＝新用户握手：唤醒即软重置（记忆清零、门保持开）。"""

    def _make_conn(self, transcript: str):
        from server.microphone_stream import MicrophoneConnection
        from server.personas import load_personas
        from services.llm_memory import ConversationMemory

        load_personas()
        opt = SimpleNamespace(
            default_tts="cosyvoice3",
            mic_reopen_grace_ms=0,
            wake_word_default="你好",
            wake_word_live="你好",
        )
        conn = MicrophoneConnection.__new__(MicrophoneConnection)
        conn.ws = FakeWS()
        conn.opt = opt
        conn.asr = FakeASR(transcript)
        conn.mode = "default"
        conn.session_id = ""
        conn.generation = 0
        conn.closed = threading.Event()
        conn.loop = asyncio.get_running_loop()
        conn.output = asyncio.Queue(maxsize=64)
        conn.reply_text = ""
        conn.last_reply_audio_at = None
        conn.pending_turn = None
        conn.pending_task = None
        conn.wake = WakeWordGate("你好", "你好")
        conn.replies = []
        conn.reply_gens = []
        # stub 记录 (文本, 代际)：真实回复工人会用代际拦截旧轮——曾发生
        # 软重置抬代际后本轮仍持旧值、问候回复被自己掐死的回归（2026-10-24）
        conn._stream_default_reply = (
            lambda text, gen, canned_reply="": (
                conn.replies.append(text), conn.reply_gens.append(gen)
            )
        )
        conn.persona_override = ""
        conn._last_rec = None
        conn._emotion_note = ""
        conn.llm_memory = ConversationMemory()
        from server.emotion_tracker import EmotionTracker

        conn.emotions = EmotionTracker(role="companion")
        conn.live_reply_id = 0
        conn.carry_audio = None
        conn._last_pcm_at = None
        conn.vad = SimpleNamespace(reset=lambda: None)
        conn._tasks = set()
        return conn

    async def _run_turn(self, conn):
        await conn._handle_turn(np.zeros(16000, dtype=np.int16), 0, "default", "")

    async def _events(self, conn):
        out = []
        while not conn.output.empty():
            payload = conn.output.get_nowait()[1]
            if not isinstance(payload, bytes):
                out.append(payload)
        return out

    async def test_hello_resets_but_keeps_gate_open(self):
        # 上一位用户的会话痕迹
        conn = self._make_conn("你好")
        conn.llm_memory.append("user", "我最近老是头疼")
        conn.emotions.feed("我最近老是头疼")
        await self._run_turn(conn)
        events = await self._events(conn)
        kinds = [e.get("type") for e in events]
        # 唤醒成功 + 会话软重置 + 门保持开（不再要一次你好）
        self.assertIn("wake", kinds)
        self.assertIn("session_reset", kinds)
        self.assertFalse(conn.wake.required("default"), "软重置后不得重新武装唤醒门")
        # 记忆/情绪/推荐全清零
        self.assertEqual([], conn.llm_memory.messages())
        self.assertEqual("neutral", conn.emotions.current)
        # 唤醒词剥掉后仍走正常回复（greet 重新待命由前端 session_reset 处理）
        self.assertIn("transcript", kinds)
        self.assertEqual(["你好"], conn.replies)
        # 回复工人拿到的代际必须是软重置后的新代际（旧值会被工人当场丢弃）
        self.assertEqual([conn.generation], conn.reply_gens, "软重置后本轮必须用新代际")

    async def test_hello_with_content_does_not_reset(self):
        conn = self._make_conn("你好，我想做个疲劳检测")
        conn.llm_memory.append("user", "我最近老是头疼")
        await self._run_turn(conn)
        events = await self._events(conn)
        kinds = [e.get("type") for e in events]
        self.assertIn("wake", kinds)
        # 带内容的唤醒不是新用户握手：记忆保留、不触发 session_reset
        self.assertNotIn("session_reset", kinds)
        self.assertTrue(conn.llm_memory.messages())
        self.assertEqual(["我想做个疲劳检测"], conn.replies)


if __name__ == "__main__":
    unittest.main()
