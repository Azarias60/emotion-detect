"""LLM 动作标签：流式解析、状态机触发、default 页 action 事件。"""

from __future__ import annotations

import asyncio
import threading
import unittest
from types import SimpleNamespace

import numpy as np

import llm as llm_module
from services.action_parser import ActionTagParser, guess_greeting_action, state_for_action


def streamed(*chunks: str):
    """Build a fake LLMService class yielding the given chunks."""

    class FakeLLMService:
        def __init__(self, opt):
            self.opt = opt

        def stream_text(self, message, system_prompt=None, memory=None):
            self.system_prompt = system_prompt
            self.message = message
            yield from chunks

    return FakeLLMService


class FakeAvatarSession:
    def __init__(self, with_state_machine: bool = True):
        self.opt = SimpleNamespace(llm_system_prompt="测试提示词")
        self.spoken: list[str] = []
        self.states: list[int] = []
        if with_state_machine:
            self.set_custom_state = self.states.append

    def put_msg_txt(self, msg, datainfo=None):
        self.spoken.append(msg)


class FakeAvatarWithoutAction(FakeAvatarSession):
    """段表里没有动作段的形象（yy 只有 comfort）：明确回 False。"""

    def __init__(self):
        super().__init__(with_state_machine=False)
        self.states = []
        self.set_custom_state = self._set_without_segment

    def _set_without_segment(self, state, reinit=True):
        self.states.append(state)
        return False


class ActionTagParserTests(unittest.TestCase):
    def feed_all(self, parser: ActionTagParser, text: str) -> str:
        body = "".join(parser.feed(ch) for ch in text)
        return body + parser.flush()

    def test_leading_tag_is_extracted_and_stripped(self):
        parser = ActionTagParser()
        out = parser.feed("[com")
        self.assertEqual("", out)  # 标签未闭合前不放行
        out += parser.feed("fort]别难过，")
        self.assertEqual("别难过，", out)
        self.assertEqual("comfort", parser.action)
        out += self.feed_all(parser, "有我在。")
        self.assertEqual("别难过，有我在。", out)

    def test_no_tag_releases_first_char_immediately(self):
        parser = ActionTagParser()
        # 首字符不是标签起始符 → 整个增量立即放行，零额外延迟
        self.assertEqual("你好呀", parser.feed("你好呀"))
        self.assertEqual("你好呀", parser.feed("你好呀"))

    def test_neutral_tag_is_stripped_without_action(self):
        body = self.feed_all(ActionTagParser(), "[idle]今天天气不错。")
        self.assertEqual("今天天气不错。", body)
        self.assertIsNone(ActionTagParser().action)

    def test_full_width_variants(self):
        for text in ("【comfort】抱抱", "（动作：praise）真棒"):
            parser = ActionTagParser()
            body = self.feed_all(parser, text)
            self.assertNotIn("[", body)
            self.assertTrue(body)

    def test_unclosed_bracket_releases_as_body(self):
        long_bracket = "[" + "这是一个远超扫描上限所以永远无法闭合的标签内容" + "]"
        body = self.feed_all(ActionTagParser(), long_bracket + "正文继续")
        self.assertIn("正文继续", body)
        self.assertIsNone(ActionTagParser().action)

    def test_state_mapping(self):
        self.assertEqual(7, state_for_action("comfort"))
        self.assertIsNone(state_for_action("idle"))
        self.assertIsNone(state_for_action(None))
        self.assertIsNone(state_for_action("unknown"))


class GreetingFallbackTests(unittest.TestCase):
    def test_greeting_messages_map_to_greet(self):
        for msg in ("你好呀", "在吗？", "晚安，明天见", "Hello!", "早上好呀"):
            self.assertEqual("greet", guess_greeting_action(msg), msg)

    def test_non_greeting_returns_none(self):
        for msg in ("我今天考砸了", "帮我加油", "今天天气不错", ""):
            self.assertIsNone(guess_greeting_action(msg), msg)

    def test_missing_tag_on_greeting_triggers_greet(self):
        session = FakeAvatarSession()
        run_llm(session, "嗨，我是暖暖，很高兴认识你！", message="你好呀")
        self.assertEqual([4], session.states)

    def test_explicit_idle_tag_blocks_greeting_fallback(self):
        session = FakeAvatarSession()
        run_llm(session, "[idle]你好呀，随便聊聊。", message="你好呀")
        self.assertEqual([], session.states)


def run_llm(session, reply: str, message: str = "用户的话"):
    """Feed `reply` through llm_response with a fake streaming service."""
    original = llm_module.LLMService
    llm_module.LLMService = streamed(reply)
    try:
        llm_module.llm_response(message, session)
    finally:
        llm_module.LLMService = original


def run_llm_with(session, reply: str, message: str = "用户的话", **kwargs):
    """Same as run_llm but forwards extra kwargs (e.g. on_action)."""
    original = llm_module.LLMService
    llm_module.LLMService = streamed(reply)
    try:
        llm_module.llm_response(message, session, **kwargs)
    finally:
        llm_module.LLMService = original


def run_llm_chunks(session, *chunks, message: str = "用户的话"):
    """Feed several streamed chunks (to prove per-delta behaviour)."""
    original = llm_module.LLMService
    llm_module.LLMService = streamed(*chunks)
    try:
        llm_module.llm_response(message, session)
    finally:
        llm_module.LLMService = original


class LlmResponseActionTests(unittest.TestCase):
    def run_llm(self, session, reply: str):
        run_llm(session, reply)

    def test_action_tag_triggers_state_machine(self):
        session = FakeAvatarSession()
        self.run_llm(session, "[comfort]别难过，我一直都在。")
        self.assertEqual([7], session.states)
        self.assertEqual("别难过，我一直都在。", "".join(session.spoken))

    def test_all_action_tags_map_to_states(self):
        for tag, state in {
            "greet": 4,
            "encourage": 5,
            "praise": 6,
            "comfort": 7,
        }.items():
            session = FakeAvatarSession()
            self.run_llm(session, f"[{tag}]正文")
            self.assertEqual([state], session.states, tag)

    def test_neutral_tag_does_not_trigger(self):
        session = FakeAvatarSession()
        self.run_llm(session, "[idle]随便聊聊。")
        self.assertEqual([], session.states)
        self.assertEqual("随便聊聊。", "".join(session.spoken))

    def test_plain_reply_untouched(self):
        session = FakeAvatarSession()
        self.run_llm(session, "今天过得怎么样？")
        self.assertEqual([], session.states)
        self.assertEqual("今天过得怎么样？", "".join(session.spoken))

    def test_first_segment_is_cut_early(self):
        session = FakeAvatarSession()
        self.run_llm(session, "好的呀，我在听呢。还有什么想聊的吗？")
        # 首段下限 8（FIRST_SEGMENT_MIN）：满 8 字且到句读才切
        self.assertEqual("好的呀，我在听呢。", session.spoken[0])
        self.assertEqual("还有什么想聊的吗？", session.spoken[1])

    def test_session_without_state_machine_is_safe(self):
        session = FakeAvatarSession(with_state_machine=False)
        self.run_llm(session, "[greet]你好呀！")
        self.assertEqual("你好呀！", "".join(session.spoken))

    def test_system_prompt_contains_action_rules(self):
        captured = {}

        class CapturingService(streamed("[greet]你好")):
            def stream_text(self, message, system_prompt=None, memory=None):
                captured["prompt"] = system_prompt
                yield from ()

        original = llm_module.LLMService
        llm_module.LLMService = CapturingService
        try:
            llm_module.llm_response("在吗", FakeAvatarSession())
        finally:
            llm_module.LLMService = original
        self.assertIn("动作标签规则", captured["prompt"])

    def test_on_action_callback_fires_for_live_ui(self):
        fired = []
        session = FakeAvatarSession()
        run_llm_with(
            session, "[comfort]别难过。", on_action=fired.append
        )
        self.assertEqual(["comfort"], fired)

    def test_on_action_not_fired_without_action(self):
        fired = []
        session = FakeAvatarSession()
        run_llm_with(session, "今天天气不错。", on_action=fired.append)
        self.assertEqual([], fired)


class MissingActionSegmentTests(unittest.TestCase):
    """形象段表缺少该动作段：标签消费掉，正文照念，且不逐 delta 重试。"""

    def test_trigger_returns_skipped_for_missing_segment(self):
        session = FakeAvatarWithoutAction()
        self.assertEqual(
            "skipped", llm_module.trigger_avatar_action(session, "greet")
        )
        self.assertEqual("none", llm_module.trigger_avatar_action(session, None))
        self.assertEqual(
            "played", llm_module.trigger_avatar_action(FakeAvatarSession(), "greet")
        )

    def test_missing_action_is_tried_once_and_body_still_spoken(self):
        session = FakeAvatarWithoutAction()
        # 两个 chunk：改前每个 delta 都会重试一次触发（→ 2 条异常堆栈）
        run_llm_chunks(session, "[greet]你好", "呀，我是暖暖。")
        self.assertEqual([4], session.states, "只应尝试一次，不逐 delta 重试")
        self.assertEqual("你好呀，我是暖暖。", "".join(session.spoken))

    def test_missing_action_does_not_fire_on_action_callback(self):
        fired = []
        session = FakeAvatarWithoutAction()
        run_llm_with(session, "[greet]你好呀。", on_action=fired.append)
        self.assertEqual([], fired, "动作没播就不应通知前端")


class FakeTTS:
    def __init__(self):
        self.texts: list[str] = []

    def _generate(self, text: str):
        self.texts.append(text)
        for _ in range(3):
            yield np.full(512, 0.1, dtype=np.float32)


class BrokenTTS:
    """每次合成都抛异常或零输出的引擎（模拟在线 TTS 故障）。"""

    def __init__(self, mode: str = "raise"):
        self.texts: list[str] = []
        self.mode = mode

    def _generate(self, text: str):
        self.texts.append(text)
        if self.mode == "raise":
            raise RuntimeError("network down")
        return
        yield  # pragma: no cover - 让函数成为生成器（零输出路径）


class FakeWS:
    def __init__(self):
        self.sent: list = []

    async def send_bytes(self, payload):
        self.sent.append(payload)

    async def send_json(self, payload):
        self.sent.append(payload)


class FlakyTTS:
    """第一次合成失败，之后成功（模拟 Edge 瞬断）。"""

    def __init__(self):
        self.texts: list[str] = []
        self.calls = 0

    def _generate(self, text: str):
        self.calls += 1
        self.texts.append(text)
        if self.calls == 1:
            raise RuntimeError("transient blip")
        for _ in range(2):
            yield np.full(512, 0.1, dtype=np.float32)


class DefaultReplyActionEventTests(unittest.IsolatedAsyncioTestCase):
    def _make_connection(self, reply: str):
        from server import microphone_stream as ms
        from server.microphone_stream import MicrophoneConnection

        opt = SimpleNamespace(
            default_tts="edge",
            default_tts_voice="test",
            mic_reopen_grace_ms=0,
        )
        loop = asyncio.get_running_loop()
        conn = MicrophoneConnection.__new__(MicrophoneConnection)
        conn.ws = FakeWS()
        conn.opt = opt
        conn.asr = None
        conn.mode = "default"
        conn.session_id = ""
        conn.generation = 0
        conn.closed = threading.Event()
        conn.loop = loop
        conn.output = asyncio.Queue(maxsize=160)
        conn.reply_text = ""
        conn.last_reply_audio_at = None
        # __init__ 会设、_stream_default_reply 会读的连接级属性（夹具须补齐）
        conn.persona_override = ""
        conn._emotion_note = ""
        conn.llm_memory = None
        tts = FakeTTS()
        conn._make_default_tts = lambda: tts
        original = ms.LLMService
        ms.LLMService = streamed(reply)
        return conn, tts, ms, original

    async def _drain(self, conn: MicrophoneConnection):
        events = []
        while not conn.output.empty():
            events.append(conn.output.get_nowait())
        return events

    async def test_action_event_emitted_and_tag_not_spoken(self):
        # 轻量页：标签剥离且不被朗读，但不再发 action 事件（动作属 live 模式）
        conn, tts, module, original = self._make_connection(
            "[encourage]没关系的，慢慢来。"
        )
        try:
            await asyncio.to_thread(conn._stream_default_reply, "我搞砸了", 0)
        finally:
            module.LLMService = original
        events = await self._drain(conn)
        actions = [
            payload for kind, payload, _ in events
            if kind == "json" and payload.get("type") == "action"
        ]
        self.assertEqual([], actions, "轻量页不应发出 action 事件")
        deltas = "".join(
            payload["text"] for kind, payload, _ in events
            if kind == "json" and payload.get("type") == "assistant_delta"
        )
        self.assertEqual("没关系的，慢慢来。", deltas)
        self.assertEqual("没关系的，慢慢来。", conn.reply_text)
        for text in tts.texts:
            self.assertNotIn("[", text)
        kinds = {payload.get("type") for kind, payload, _ in events if kind == "json"}
        self.assertIn("response_done", kinds)
        self.assertTrue(
            any(kind == "binary" for kind, _, _ in events),
            "语音块应已入队",
        )

    async def test_cancelled_generation_drops_reply(self):
        conn, _, module, original = self._make_connection("[greet]你好")
        conn.generation = 5
        try:
            await asyncio.to_thread(conn._stream_default_reply, "在吗", 0)
        finally:
            module.LLMService = original
        events = await self._drain(conn)
        self.assertEqual([], events)

    async def test_first_segment_reaches_tts_immediately(self):
        conn, tts, module, original = self._make_connection(
            "[comfort]没事的，慢慢来。都会好起来的。"
        )
        try:
            await asyncio.to_thread(conn._stream_default_reply, "我搞砸了", 0)
        finally:
            module.LLMService = original
        # 首段下限 6（FIRST_SEGMENT_MIN）："没事的，"仅 4 字不切，
        # 到第一个满 6 字的句读"没事的，慢慢来。"才送 TTS
        self.assertEqual("没事的，慢慢来。", tts.texts[0])
        self.assertEqual("都会好起来的。", tts.texts[1])

    async def test_action_tag_is_stripped_without_event_on_default_page(self):
        # 动作是实时口型专属：轻量页剥离标签（不朗读）但不发 action 事件
        conn, tts, module, original = self._make_connection(
            "[comfort]没事的，慢慢来。"
        )
        try:
            await asyncio.to_thread(conn._stream_default_reply, "我搞砸了", 0)
        finally:
            module.LLMService = original
        events = await self._drain(conn)
        actions = [
            payload
            for kind, payload, _ in events
            if kind == "json" and payload.get("type") == "action"
        ]
        self.assertEqual([], actions, "轻量页不应发出 action 事件")
        self.assertEqual("没事的，慢慢来。", "".join(tts.texts))
        self.assertEqual("没事的，慢慢来。", conn.reply_text)

    async def test_greeting_on_default_page_has_no_action_event(self):
        conn, tts, module, original = self._make_connection(
            "嗨，我是暖暖，很高兴认识你！"
        )
        try:
            await asyncio.to_thread(conn._stream_default_reply, "你好呀", 0)
        finally:
            module.LLMService = original
        events = await self._drain(conn)
        actions = [
            payload
            for kind, payload, _ in events
            if kind == "json" and payload.get("type") == "action"
        ]
        self.assertEqual([], actions, "轻量页问候不应触发 action 事件")
        self.assertTrue(tts.texts, "回复照常合成")

    async def test_transient_edge_failure_retries_without_fallback(self):
        conn, _, module, original = self._make_connection(
            "[comfort]没事呀。"  # 单段短句，整轮只有一次合成
        )
        flaky = FlakyTTS()
        conn._make_default_tts = lambda: flaky
        try:
            await asyncio.to_thread(conn._stream_default_reply, "我搞砸了", 0)
        finally:
            module.LLMService = original
        events = await self._drain(conn)
        fallbacks = [
            payload
            for kind, payload, _ in events
            if kind == "json" and payload.get("type") == "tts_fallback"
        ]
        self.assertEqual([], fallbacks, "瞬断重试成功不应触发降级事件")
        self.assertEqual(2, flaky.calls, "失败后应原地重试一次")
        self.assertTrue(
            any(kind == "binary" for kind, _, _ in events),
            "重试成功后应产出语音块",
        )

    async def test_persistent_edge_failure_uses_windows_voice(self):
        for mode in ("raise", "empty"):
            with self.subTest(mode=mode):
                conn, _, module, original = self._make_connection(
                    "[comfort]没事呀。"
                )
                engine = BrokenTTS(mode)
                conn._make_default_tts = lambda: engine
                win = FakeTTS()
                import server.microphone_stream as ms

                original_win = ms._windows_fallback_tts
                ms._windows_fallback_tts = lambda: win
                try:
                    await asyncio.to_thread(
                        conn._stream_default_reply, "我搞砸了", 0
                    )
                finally:
                    module.LLMService = original
                    ms._windows_fallback_tts = original_win
                events = await self._drain(conn)
                fallbacks = [
                    payload
                    for kind, payload, _ in events
                    if kind == "json" and payload.get("type") == "tts_fallback"
                ]
                self.assertEqual(
                    [{"type": "tts_fallback", "to": "windows"}], fallbacks
                )
                self.assertEqual(2, len(engine.texts), "应原地重试一次后兜底")
                self.assertTrue(win.texts, "Windows 语音应接到该段")
                self.assertTrue(
                    any(kind == "binary" for kind, _, _ in events),
                    "兜底后应产出语音块",
                )
                kinds = {
                    payload.get("type")
                    for kind, payload, _ in events
                    if kind == "json"
                }
                self.assertIn("response_done", kinds)


if __name__ == "__main__":
    unittest.main()
