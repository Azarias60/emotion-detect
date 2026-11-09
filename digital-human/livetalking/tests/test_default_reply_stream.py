"""默认页回复管线的回归测试。

`_stream_default_reply` 是生产者/消费者结构：调用线程持续读 LLM 流并把文字
delta 发给页面，语音段进有界队列，由单独的 worker 线程按顺序合成。这里锁住
五个曾出过（或最容易回归出）问题的行为：

1. 顺序 —— 语音块按切段顺序出声，不会乱序；
2. 解耦 —— TTS 卡住时 LLM 文字照常接收（文字输出不被语音拖住）；
3. 打断 —— 新一轮取消后生产者和 worker 都要停，不残留 response_done；
4. 不重复 —— 段内合成中断不整段重试（重试会把开头再念一遍）；
5. 不哑 —— 主引擎持续失败时逐段退到本地 Windows 语音。
"""

from __future__ import annotations

import asyncio
import threading
import unittest
from types import SimpleNamespace
from unittest import mock

import numpy as np

import server.microphone_stream as mic_module
from server.microphone_stream import MicrophoneConnection
from services.llm_memory import ConversationMemory
from services.text_slicer import SegmentSlicer


# 默认页切块参数（与 _stream_default_reply 内部一致），测试里用它算期望段
def _reference_segments(text: str) -> list[str]:
    slicer = SegmentSlicer(
        mic_module.FIRST_SEGMENT_MIN,
        mic_module.FIRST_SEGMENT_MAX,
        mic_module.FIRST_SEGMENT_MAX + 8,
        mic_module.SEGMENT_MIN,
        mic_module.SEGMENT_MAX,
        mic_module.SEGMENT_MAX + 24,
        first_weak_min=mic_module.FIRST_SEGMENT_MIN,
        weak_min=14,
    )
    segments: list[str] = []
    for ch in text:
        segments.extend(slicer.feed(ch))
    tail = slicer.flush()
    if tail:
        segments.append(tail)
    return segments


class FakeWS:
    def __init__(self) -> None:
        self.sent: list = []

    async def send_bytes(self, payload) -> None:
        self.sent.append(payload)

    async def send_json(self, payload) -> None:
        self.sent.append(payload)


class FakeASR:
    def transcribe_pcm(self, pcm) -> str:
        return ""


class FakeDefaultTTS:
    """按段合成假音频：每段 700 个恒定值样本（可按值区分段落顺序）。"""

    def __init__(self, *, block_first=False, fail_first_midway=False,
                 always_empty=False, chunked=False):
        self.requested: list[str] = []
        self.block_gate = threading.Event()
        self.release = threading.Event()
        self.block_first = block_first
        self.fail_first_midway = fail_first_midway
        self.always_empty = always_empty
        self.chunked = chunked

    def _samples(self, index: int) -> np.ndarray:
        value = 0.004 * index
        return np.full(700, value, dtype=np.float32)

    def _generate(self, text: str):
        self.requested.append(text)
        index = len(self.requested)
        if self.always_empty:
            return
        if self.block_first and index == 1:
            self.block_gate.set()
            self.release.wait(timeout=10)
        if self.fail_first_midway and index == 1:
            yield np.full(300, 0.002, dtype=np.float32)
            raise RuntimeError("段内合成中断")
        if self.chunked:
            for _ in range(10):
                yield np.full(200, 0.003, dtype=np.float32)
            return
        yield self._samples(index)


class FakeLLMService:
    """按 delta 列表吐字；记录调用参数供记忆接线断言。"""

    calls: list[dict] = []

    def __init__(self, opt):
        self.opt = opt

    def stream_text(self, message, system_prompt=None, memory=None):
        FakeLLMService.calls.append(
            {"message": message, "system_prompt": system_prompt, "memory": memory}
        )
        for delta in self.deltas:
            yield delta

    deltas: list[str] = []


async def _drain(output: asyncio.Queue, *, idle_s: float = 0.2) -> list[tuple]:
    """取空事件队列（_stream_default_reply 返回前 worker 已 join，无并发写）。"""
    events: list[tuple] = []
    while True:
        try:
            events.append(await asyncio.wait_for(output.get(), timeout=idle_s))
        except asyncio.TimeoutError:
            return events


def _decoded_audio(events: list[tuple]) -> np.ndarray:
    blocks = [
        np.frombuffer(payload[1:], dtype="<i2")
        for kind, payload, _gen in events
        if kind == "binary"
    ]
    return np.concatenate(blocks) if blocks else np.empty(0, "<i2")


def _runs(samples: np.ndarray) -> list[tuple[int, int]]:
    """等值游程序列 [(值, 个数), ...]，用于断言段落顺序。"""
    if samples.size == 0:
        return []
    change = np.flatnonzero(np.diff(samples)) + 1
    bounds = np.concatenate(([0], change, [samples.size]))
    return [(int(samples[a]), int(b - a)) for a, b in zip(bounds, bounds[1:])]


class DefaultReplyStreamTests(unittest.IsolatedAsyncioTestCase):
    async def _make_conn(self) -> MicrophoneConnection:
        return MicrophoneConnection(FakeWS(), SimpleNamespace(), FakeASR())

    @staticmethod
    def _json_events(events: list[tuple]) -> list[dict]:
        return [payload for kind, payload, _gen in events if kind == "json"]

    async def test_segments_synthesized_in_order_and_merged(self):
        text = "我今天心情挺平静的，像晒过太阳的被子，暖暖的，你呢？"
        expected = _reference_segments(text)
        # 曾把每个逗号切成 6~8 字碎片；合段后这句最多 3 段
        self.assertLessEqual(len(expected), 3)

        tts = FakeDefaultTTS()
        FakeLLMService.deltas = list(text)
        FakeLLMService.calls = []
        conn = await self._make_conn()
        conn._make_default_tts = lambda: tts
        with mock.patch.object(mic_module, "LLMService", FakeLLMService):
            await asyncio.to_thread(conn._stream_default_reply, "你好", conn.generation)

        events = await _drain(conn.output)
        # 段落合并：TTS 收到的正是参考切段，逐字符流式下没有额外碎片
        self.assertEqual(expected, tts.requested)
        # 语音块按段顺序出声：每段 700 样本、值随段号递增
        runs = _runs(_decoded_audio(events))
        self.assertEqual(
            [(int(np.float32(0.004 * i) * 32768), 700) for i in range(1, len(expected) + 1)],
            runs,
        )
        # 文字完整送达页面，且收尾事件存在
        jsons = self._json_events(events)
        self.assertEqual(
            text, "".join(j["text"] for j in jsons if j.get("type") == "assistant_delta")
        )
        self.assertEqual("response_done", jsons[-1]["type"])

    async def test_llm_text_is_not_blocked_by_slow_tts(self):
        text = "我今天心情挺平静的，像晒过太阳的被子，暖暖的，你呢？"
        tts = FakeDefaultTTS(block_first=True)
        FakeLLMService.deltas = list(text)
        FakeLLMService.calls = []
        conn = await self._make_conn()
        conn._make_default_tts = lambda: tts

        with mock.patch.object(mic_module, "LLMService", FakeLLMService):
            worker = threading.Thread(
                target=conn._stream_default_reply, args=("你好", conn.generation)
            )
            worker.start()
            # TTS 在第一段上卡住期间，全部文字 delta 必须已经送达页面
            got = ""
            for _ in range(200):
                while not conn.output.empty():
                    kind, payload, _gen = conn.output.get_nowait()
                    if kind == "json" and payload.get("type") == "assistant_delta":
                        got += payload["text"]
                if got == text and tts.block_gate.is_set():
                    break
                await asyncio.sleep(0.05)
            self.assertEqual(text, got, "文字接收不能被语音合成拖住")
            self.assertTrue(tts.block_gate.is_set())
            self.assertFalse(tts.release.is_set(), "此时 TTS 应仍卡在第一段")

            tts.release.set()
            # 不能用 worker.join()：它会阻塞事件循环，而 worker 正等着
            # 往这个循环投递音频事件（await 让 put 协程得以执行）
            for _ in range(200):
                if not worker.is_alive():
                    break
                await asyncio.sleep(0.05)
            self.assertFalse(worker.is_alive())

        events = await _drain(conn.output, idle_s=0.3)
        self.assertEqual("response_done", self._json_events(events)[-1]["type"])
        self.assertGreater(_decoded_audio(events).size, 0)

    async def test_cancel_mid_reply_stops_producer_and_worker(self):
        text = "我今天心情挺挺平静的，像晒过太阳的被子，暖暖的，你呢？"
        tts = FakeDefaultTTS(chunked=True)
        FakeLLMService.deltas = list(text)
        FakeLLMService.calls = []
        conn = await self._make_conn()
        conn._make_default_tts = lambda: tts

        with mock.patch.object(mic_module, "LLMService", FakeLLMService):
            worker = threading.Thread(
                target=conn._stream_default_reply, args=("你好", conn.generation)
            )
            worker.start()
            # 等到语音真正开始出块再打断
            seen: list[tuple] = []
            for _ in range(200):
                while not conn.output.empty():
                    seen.append(conn.output.get_nowait())
                if any(kind == "binary" for kind, _p, _g in seen):
                    break
                await asyncio.sleep(0.05)
            conn._cancel_current_response()
            worker.join(timeout=10)
            self.assertFalse(worker.is_alive(), "打断后 worker 必须及时退出")
            events = await _drain(conn.output)
            events = seen + events
            jsons = self._json_events(events)
            self.assertNotIn(
                "response_done", [j.get("type") for j in jsons],
                "被取消的回复不得宣告完成",
            )
            # 线程已退出、队列不再增长
            await asyncio.sleep(0.2)
            self.assertEqual(0, conn.output.qsize(), "打断后不得再产生新事件")

    async def test_partial_segment_failure_is_not_retried(self):
        # 首段合成中途失败：已送出的样本不重念（不重复），后续段照常合成
        text = "我今天心情挺平静的，像晒过太阳的被子，暖暖的，你呢？"
        expected = _reference_segments(text)
        tts = FakeDefaultTTS(fail_first_midway=True)
        FakeLLMService.deltas = list(text)
        FakeLLMService.calls = []
        conn = await self._make_conn()
        conn._make_default_tts = lambda: tts
        with mock.patch.object(mic_module, "LLMService", FakeLLMService):
            await asyncio.to_thread(conn._stream_default_reply, "你好", conn.generation)

        # 每段只请求一次 —— 失败段没有整段重试
        self.assertEqual(expected, tts.requested)
        self.assertEqual(
            len(expected), len(tts.requested), "失败段不得重复合成（听感重复）"
        )
        events = await _drain(conn.output)
        self.assertEqual("response_done", self._json_events(events)[-1]["type"])
        # 后续段音频仍然出声（不缺失）
        self.assertGreater(_decoded_audio(events).size, 0)

    async def test_persistent_failure_falls_back_to_windows_voice(self):
        text = "我今天心情挺平静的，像晒过太阳的被子，暖暖的，你呢？"
        expected = _reference_segments(text)
        tts = FakeDefaultTTS(always_empty=True)
        fallbacks: list[FakeDefaultTTS] = []
        FakeLLMService.deltas = list(text)
        FakeLLMService.calls = []
        conn = await self._make_conn()
        conn._make_default_tts = lambda: tts
        with mock.patch.object(mic_module, "LLMService", FakeLLMService), \
                mock.patch.object(
                    mic_module, "_windows_fallback_tts",
                    lambda: fallbacks.append(FakeDefaultTTS()) or fallbacks[-1],
                ):
            await asyncio.to_thread(conn._stream_default_reply, "你好", conn.generation)

        # 主引擎"0 样本原地重试一次"是设计行为（此时没播出任何音频，
        # 重试安全）：每段先由主引擎请求两次
        self.assertEqual(
            [seg for seg in expected for _ in range(2)], tts.requested
        )
        self.assertTrue(fallbacks, "持续失败必须逐段退到本地语音，不能哑掉")
        self.assertEqual(
            expected, [seg for fb in fallbacks for seg in fb.requested]
        )
        events = await _drain(conn.output)
        jsons = self._json_events(events)
        self.assertEqual(1, sum(1 for j in jsons if j.get("type") == "tts_fallback"))
        self.assertEqual("response_done", jsons[-1]["type"])

    async def test_memory_is_wired_into_default_reply(self):
        tts = FakeDefaultTTS()
        FakeLLMService.deltas = ["你好呀。", "我在听。"]
        FakeLLMService.calls = []
        conn = await self._make_conn()
        conn._make_default_tts = lambda: tts
        with mock.patch.object(mic_module, "LLMService", FakeLLMService):
            await asyncio.to_thread(conn._stream_default_reply, "我叫小明", conn.generation)

        self.assertEqual(1, len(FakeLLMService.calls))
        call = FakeLLMService.calls[0]
        self.assertEqual("我叫小明", call["message"])
        self.assertIs(conn.llm_memory, call["memory"])
        self.assertIsInstance(call["memory"], ConversationMemory)


if __name__ == "__main__":
    unittest.main()
