"""回复代际：打断后旧回复线程送来的段落必须被 TTS 丢弃（防重叠/重复）。"""

from __future__ import annotations

import threading
import time
import unittest
from queue import Queue
from types import SimpleNamespace

import llm as llm_module
from tts.base_tts import BaseTTS


def make_tts(parent_epoch: int) -> BaseTTS:
    tts = BaseTTS.__new__(BaseTTS)
    tts.opt = None
    tts.parent = SimpleNamespace(_reply_epoch=parent_epoch)
    tts.sample_rate = 16000
    tts.chunk = 320
    tts.input_stream = None
    tts.msgqueue = Queue()
    tts.state = None
    tts.busy = False
    tts.last_error = None
    tts.processed = []
    tts.txt_to_audio = lambda msg: tts.processed.append(msg[0])
    tts.stop_tts = lambda: None
    return tts


class StaleReplyEpochTests(unittest.TestCase):
    def test_stale_segments_are_dropped(self):
        tts = make_tts(parent_epoch=2)
        quit_event = threading.Event()
        worker = threading.Thread(target=tts.process_tts, args=(quit_event,), daemon=True)
        worker.start()

        tts.put_msg_txt("旧回复的段落", {"reply_epoch": 1})   # 打断前代际 → 丢弃
        tts.put_msg_txt("新回复的段落", {"reply_epoch": 2})   # 当代 → 播报

        deadline = time.monotonic() + 3
        while not tts.processed and time.monotonic() < deadline:
            time.sleep(0.02)
        quit_event.set()
        worker.join(timeout=2)

        self.assertEqual(["新回复的段落"], tts.processed)

    def test_segments_without_epoch_always_play(self):
        # 兼容旧调用方（不带 reply_epoch 的消息不丢弃）
        tts = make_tts(parent_epoch=5)
        quit_event = threading.Event()
        worker = threading.Thread(target=tts.process_tts, args=(quit_event,), daemon=True)
        worker.start()

        tts.put_msg_txt("echo 直播的段落", {})

        deadline = time.monotonic() + 3
        while not tts.processed and time.monotonic() < deadline:
            time.sleep(0.02)
        quit_event.set()
        worker.join(timeout=2)

        self.assertEqual(["echo 直播的段落"], tts.processed)


class FakeReplySession:
    """带代际的最小会话：模拟"回复进行到一半被打断"。"""

    def __init__(self) -> None:
        self.opt = SimpleNamespace(llm_system_prompt="测试提示词")
        self.epoch = 0
        self.stamped: list[tuple[str, int | None]] = []
        self.states: list[int] = []

    def begin_reply(self) -> int:
        return self.epoch

    def set_custom_state(self, state, reinit=True):
        self.states.append(state)
        return None

    def put_msg_txt(self, msg, datainfo=None):
        epoch = (datainfo or {}).get("reply_epoch")
        self.stamped.append((msg, epoch))


def streamed_interrupting(chunks, session, interrupt_before_index):
    """Fake LLMService：在第 N 个 chunk 之前抬一次代际（用户打断）。"""

    class FakeLLMService:
        def __init__(self, opt):
            self.opt = opt

        def stream_text(self, message, system_prompt=None, memory=None):
            for index, chunk in enumerate(chunks):
                if index == interrupt_before_index:
                    session.epoch += 1  # flush_talk() 抬代际
                yield chunk

    return FakeLLMService


class ReplyEpochStampingTests(unittest.TestCase):
    """代际必须在**回复开始**时锁定，而不是段落入队时现取。

    若在入队时现取 ``_reply_epoch``，被打断的旧回复线程在打断*之后*送来的
    段落会盖上打断后的新代际，判定"不过期"照念——新旧两轮回复的语音叠着
    播，听感就是"回复重复、一直在说、互相重叠"。
    """

    def _run(self, chunks, interrupt_before_index):
        session = FakeReplySession()
        original = llm_module.LLMService
        llm_module.LLMService = streamed_interrupting(
            chunks, session, interrupt_before_index
        )
        try:
            llm_module.llm_response("你好呀", session)
        finally:
            llm_module.LLMService = original
        return session

    def test_segments_keep_the_epoch_captured_at_reply_start(self):
        session = self._run(
            ["[greet]你好呀，", "我是暖暖。"],
            interrupt_before_index=1,
        )
        # 打断确实发生了（代际被抬到 1）
        self.assertEqual(1, session.epoch)
        # 但本轮段落仍带回复开始时的代际 0 → TTS 会按"过期"丢弃
        self.assertEqual([("你好呀，我是暖暖。", 0)], session.stamped)

    def test_uninterrupted_reply_keeps_current_epoch(self):
        session = self._run(["你好呀，", "我是暖暖。"], interrupt_before_index=99)
        self.assertEqual([("你好呀，我是暖暖。", 0)], session.stamped)

    def test_session_without_begin_reply_is_supported(self):
        # 旧调用方（没有 begin_reply）不能因为新参数而崩
        class LegacySession(FakeReplySession):
            begin_reply = None

        session = LegacySession()
        original = llm_module.LLMService
        llm_module.LLMService = streamed_interrupting(["你好呀，"], session, 99)
        try:
            llm_module.llm_response("在吗", session)
        finally:
            llm_module.LLMService = original
        self.assertEqual([("你好呀，", None)], session.stamped)

    def test_should_stop_halts_the_reply_before_more_segments(self):
        session = FakeReplySession()
        seen: list[str] = []

        def should_stop() -> bool:
            # 第一段送出去之后就判定"已被取代"
            return bool(seen)

        original = llm_module.LLMService
        llm_module.LLMService = streamed_interrupting(
            ["[greet]你好呀，我是暖暖。", "今天过得怎么样？"], session, 99
        )
        try:
            llm_module.llm_response(
                "你好呀", session, on_text=seen.append, should_stop=should_stop
            )
        finally:
            llm_module.LLMService = original
        self.assertEqual(["你好呀，我是暖暖。"], seen, "被取代后不应再供段")
        self.assertEqual(1, len(session.stamped))


if __name__ == "__main__":
    unittest.main()
