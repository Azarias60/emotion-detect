"""实时口型模式的回复闸门。

live 的段落是直接灌进会话 TTS 队列的（不经过 WebSocket 代际过滤），所以
必须有一条独立通道告诉"被新一轮取代的那轮 llm_response"立刻停手；同时
要把"数字人正在说什么"记下来，否则麦克风收回来的回声会被当成用户提问，
数字人自问自答、一直说个不停。
"""

from __future__ import annotations

import asyncio
import threading
import unittest
from types import SimpleNamespace

import numpy as np

import llm as llm_module
from server.microphone_stream import MicrophoneConnection
from server.session_manager import session_manager
from services.wake_word import WakeWordGate


def _bare_connection() -> MicrophoneConnection:
    conn = MicrophoneConnection.__new__(MicrophoneConnection)
    conn.session_id = ""
    conn.generation = 0
    conn.live_reply_id = 0
    conn.closed = threading.Event()
    conn.reply_text = ""
    conn.last_reply_audio_at = None
    # __init__ 会设、_handle_turn 路径会读的连接级属性（绕过 __init__ 的
    # 夹具必须补齐，缺一个属性整轮就被异常打断成 error 事件）
    conn.persona_override = ""
    conn._last_rec = None
    conn._emotion_note = ""
    conn.llm_memory = None
    from server.emotion_tracker import EmotionTracker

    conn.emotions = EmotionTracker(role="companion")
    # LiveReplyGateTests 只验证 reply_text 累积与 should_stop 旗标；
    # on_text 里的 assistant_delta 推送与运行中的事件循环强耦合，打桩掉
    conn._emit_from_thread = lambda *args, **kwargs: True
    return conn


class LiveReplyGateTests(unittest.TestCase):
    def test_new_reply_supersedes_the_previous_one(self):
        conn = _bare_connection()
        on_text_a, stop_a = conn._begin_live_reply(generation=0)
        self.assertFalse(stop_a())
        on_text_a("第一轮回复的正文。")
        self.assertEqual("第一轮回复的正文。", conn.reply_text)

        _on_text_b, stop_b = conn._begin_live_reply(generation=0)
        self.assertTrue(stop_a(), "上一轮回复必须被告知停止供段")
        self.assertFalse(stop_b())
        self.assertEqual("", conn.reply_text, "新一轮开始应清空回声追踪文本")

    def test_cancel_stops_the_reply_in_flight(self):
        conn = _bare_connection()
        _on_text, stop = conn._begin_live_reply(generation=0)
        conn._cancel_current_response()
        self.assertTrue(stop())

    def test_generation_bump_stops_the_reply(self):
        conn = _bare_connection()
        _on_text, stop = conn._begin_live_reply(generation=0)
        conn.generation += 1
        self.assertTrue(stop())

    def test_closed_connection_stops_the_reply(self):
        conn = _bare_connection()
        _on_text, stop = conn._begin_live_reply(generation=0)
        conn.closed.set()
        self.assertTrue(stop())

    def test_on_text_accumulates_echo_reference(self):
        conn = _bare_connection()
        on_text, _stop = conn._begin_live_reply(generation=0)
        on_text("你好呀，")
        on_text("我是暖暖。")
        self.assertEqual("你好呀，我是暖暖。", conn.reply_text)
        self.assertIsNotNone(conn.last_reply_audio_at)

    def test_seconds_since_reply_audio(self):
        conn = _bare_connection()
        # 本次连接还没说过话 → None（ghost 判定直接放行）
        self.assertIsNone(conn._seconds_since_reply_audio("live"))
        on_text, _stop = conn._begin_live_reply(generation=0)
        on_text("你好。")
        self.assertLess(conn._seconds_since_reply_audio("live"), 1.0)


class FakeLiveSession:
    """最小 live 会话：带代际的 TTS 队列语义。"""

    def __init__(self) -> None:
        self.opt = SimpleNamespace(llm_system_prompt="测试提示词")
        self.epoch = 0
        self.played: list[tuple[str, int | None]] = []
        self.flushed = 0
        self.output = SimpleNamespace(clear_buffer=lambda: None)
        self._speaking = False

    def begin_reply(self) -> int:
        return self.epoch

    def flush_talk(self) -> None:
        # 打断：抬代际 + 清空还没播的段落（真实实现清的是 TTS 队列）
        self.epoch += 1
        self.flushed += 1

    def put_msg_txt(self, msg, datainfo=None) -> None:
        epoch = (datainfo or {}).get("reply_epoch")
        if epoch is not None and epoch < self.epoch:
            return  # 过期段落被 TTS 丢弃（base_tts.process_tts 的判定）
        self.played.append((msg, epoch))

    def is_speaking(self) -> bool:
        return self._speaking


class FakeWS:
    def __init__(self) -> None:
        self.sent: list = []

    async def send_bytes(self, payload) -> None:
        self.sent.append(payload)

    async def send_json(self, payload) -> None:
        self.sent.append(payload)


class FakeASR:
    def __init__(self, transcript: str) -> None:
        self.transcript = transcript

    def transcribe_pcm(self, pcm) -> str:
        return self.transcript


def interrupting_llm(chunks, interrupt, at_index=1):
    """Fake LLMService：吐到第 at_index 个 chunk 前触发一次打断，再继续吐。"""

    class FakeLLMService:
        def __init__(self, opt):
            self.opt = opt

        def stream_text(self, message, system_prompt=None, memory=None):
            for index, chunk in enumerate(chunks):
                if interrupt is not None and index == at_index:
                    interrupt()
                yield chunk

    return FakeLLMService


class LiveTurnInterruptTests(unittest.IsolatedAsyncioTestCase):
    """整轮 live 回合的端到端：中途被打断时不得留下任何段落。"""

    async def _run_turn(self, chunks, transcript="你好呀", interrupt=True):
        conn = _bare_connection()
        conn.opt = SimpleNamespace(mic_reopen_grace_ms=0)
        conn.ws = FakeWS()
        conn.asr = FakeASR(transcript)
        conn.mode = "live"
        conn.loop = asyncio.get_running_loop()
        conn.output = asyncio.Queue(maxsize=160)
        conn.wake = WakeWordGate("", "")  # 门控关闭，回合直接放行
        conn.vad = None
        conn.pending_turn = None
        conn.pending_task = None
        conn.carry_audio = None

        session_id = "test-live-session"
        conn.session_id = session_id
        session = FakeLiveSession()
        session_manager.add_session(session_id, session)

        original = llm_module.LLMService
        llm_module.LLMService = interrupting_llm(
            chunks,
            conn._cancel_current_response if interrupt else None,
        )
        try:
            await conn._handle_turn(
                np.zeros(16000, dtype="<i2"), conn.generation, "live", session_id
            )
        finally:
            llm_module.LLMService = original
            session_manager.remove_session(session_id)
        return conn, session

    async def test_interrupted_reply_leaves_no_segments(self):
        conn, session = await self._run_turn(
            ["[greet]你好呀，我是暖暖。", "今天过得怎么样？"]
        )
        # 打断确实发生了（flush_talk 抬了代际）
        self.assertEqual(1, session.flushed)
        self.assertEqual(1, session.epoch)
        # 打断前的段落照常播；打断后的段落一律不得入队（既不重复也不重叠）
        self.assertEqual([("你好呀，我是暖暖。", 0)], session.played)
        # 开本轮回复 +1、打断 +1
        self.assertEqual(2, conn.live_reply_id)
        self.assertTrue(conn.generation >= 1)

    async def test_uninterrupted_reply_plays_everything(self):
        conn, session = await self._run_turn(
            ["[greet]你好呀，我是暖暖。", "今天过得怎么样？"], interrupt=False
        )
        self.assertEqual(0, session.flushed)
        self.assertEqual(
            ["你好呀，我是暖暖。", "今天过得怎么样？"],
            [text for text, _epoch in session.played],
        )
        self.assertEqual([0, 0], [epoch for _text, epoch in session.played])

    async def test_echo_of_the_reply_is_tracked_for_suppression(self):
        conn, _session = await self._run_turn(["[greet]你好呀，我是暖暖。"])
        self.assertEqual("你好呀，我是暖暖。", conn.reply_text)
        self.assertLess(conn._seconds_since_reply_audio("live"), 1.0)


if __name__ == "__main__":
    unittest.main()
