"""多轮会话记忆：滚动窗口裁剪、动作标签剥离、两条链路都真的带上历史。"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest import mock

from services import llm_memory
from services.llm_memory import ConversationMemory, strip_leading_tag
from services.llm_service import LLMService, is_local_endpoint


class StripTagTests(unittest.TestCase):
    def test_removes_ascii_bracket_tag(self):
        self.assertEqual("你好呀。", strip_leading_tag("[comfort] 你好呀。"))

    def test_removes_fullwidth_brackets(self):
        self.assertEqual("你好呀。", strip_leading_tag("【greet】你好呀。"))
        self.assertEqual("你好呀。", strip_leading_tag("（praise）你好呀。"))

    def test_only_strips_the_leading_tag(self):
        # 正文里的方括号不能动（万一模型真说了括号内容）
        self.assertEqual("我说 [A] 和 [B]。", strip_leading_tag("我说 [A] 和 [B]。"))

    def test_plain_text_untouched(self):
        self.assertEqual("今天天气不错。", strip_leading_tag("今天天气不错。"))

    def test_empty(self):
        self.assertEqual("", strip_leading_tag(""))
        self.assertEqual("", strip_leading_tag(None))


class ConversationMemoryTests(unittest.TestCase):
    def test_append_and_messages(self):
        m = ConversationMemory(max_turns=4, max_chars=1000)
        m.append("user", "我叫小明")
        m.append("assistant", "你好小明")
        self.assertEqual(
            [
                {"role": "user", "content": "我叫小明"},
                {"role": "assistant", "content": "你好小明"},
            ],
            m.messages(),
        )
        self.assertEqual(1, m.turns())

    def test_blank_content_is_ignored(self):
        m = ConversationMemory()
        m.append("user", "   ")
        m.append("assistant", "")
        self.assertEqual([], m.messages())

    def test_trim_by_turns(self):
        m = ConversationMemory(max_turns=2, max_chars=10_000)
        for i in range(5):
            m.append("user", f"问{i}")
            m.append("assistant", f"答{i}")
        # 只保留最近 2 轮
        self.assertEqual(4, len(m))
        self.assertEqual("问3", m.messages()[0]["content"])
        self.assertEqual("答4", m.messages()[-1]["content"])

    def test_trim_by_chars(self):
        m = ConversationMemory(max_turns=50, max_chars=200)
        for i in range(50):
            m.append("user", "字" * 50)
            m.append("assistant", "回" * 50)
        total = sum(len(x["content"]) for x in m.messages())
        self.assertLessEqual(total, 200 + 100)  # 允许最后一条略微超出
        self.assertGreater(len(m), 0)

    def test_clear(self):
        m = ConversationMemory()
        m.append("user", "你好")
        m.clear()
        self.assertEqual([], m.messages())
        self.assertEqual(0, m.turns())


class _FakeDelta:
    def __init__(self, content):
        self.content = content


class _FakeChoice:
    def __init__(self, content):
        self.delta = _FakeDelta(content)


class _FakeChunk:
    def __init__(self, content):
        self.choices = [_FakeChoice(content)] if content is not None else []


class _FakeCompletions:
    def __init__(self, chunks, sink):
        self._chunks = chunks
        self._sink = sink

    def create(self, **kwargs):
        self._sink.append(kwargs)
        return iter([_FakeChunk(c) for c in self._chunks])


class _FakeClient:
    def __init__(self, chunks):
        self.calls: list[dict] = []
        self.chat = SimpleNamespace(
            completions=_FakeCompletions(chunks, self.calls)
        )


class StreamTextMemoryTests(unittest.TestCase):
    def _service(self, chunks):
        opt = SimpleNamespace(llm_api_key="k", llm_base_url=None,
                              llm_model="m", llm_system_prompt="系统")
        with mock.patch.object(llm_memory, "__name__", llm_memory.__name__):
            svc = LLMService(opt)
        client = _FakeClient(chunks)
        svc.client = client
        return svc, client

    def test_first_call_has_no_history(self):
        svc, client = self._service(["你", "好"])
        mem = ConversationMemory()
        out = "".join(svc.stream_text("你好", memory=mem))
        self.assertEqual("你好", out)
        sent = client.calls[0]["messages"]
        self.assertEqual(["system", "user"], [m["role"] for m in sent])
        # 本轮写回历史
        self.assertEqual(2, len(mem))

    def test_second_call_carries_history(self):
        svc, client = self._service(["你好小明"])
        mem = ConversationMemory()
        list(svc.stream_text("我叫小明", memory=mem))
        list(svc.stream_text("我叫什么", memory=mem))
        sent = client.calls[1]["messages"]
        self.assertEqual(
            ["system", "user", "assistant", "user"],
            [m["role"] for m in sent],
        )
        self.assertEqual("我叫小明", sent[1]["content"])
        self.assertEqual("你好小明", sent[2]["content"])
        self.assertEqual("我叫什么", sent[3]["content"])

    def test_action_tag_not_written_into_history(self):
        svc, _ = self._service(["[comfort] ", "别难过", "。"])
        mem = ConversationMemory()
        list(svc.stream_text("我好难过", memory=mem))
        self.assertEqual("别难过。", mem.messages()[1]["content"])

    def test_history_written_even_if_stream_aborted(self):
        # 模拟被"新一轮取代"提前中断：只消费一个 chunk 就关掉生成器
        svc, _ = self._service(["半", "句"])
        mem = ConversationMemory()
        gen = svc.stream_text("我说了一句话", memory=mem)
        next(gen)
        gen.close()
        # 用户说的话必须留下，否则打断一次就失忆
        self.assertEqual("user", mem.messages()[0]["role"])
        self.assertEqual("我说了一句话", mem.messages()[0]["content"])

    def test_without_memory_behaves_as_before(self):
        svc, client = self._service(["好"])
        list(svc.stream_text("你好"))
        self.assertEqual(2, len(client.calls[0]["messages"]))


class LocalEndpointTests(unittest.TestCase):
    """本地/自建端点（Ollama 等）不需要 API key，不能因为 key 为空就报错。"""

    def test_is_local_endpoint(self):
        self.assertTrue(is_local_endpoint("http://localhost:11434/v1"))
        self.assertTrue(is_local_endpoint("http://127.0.0.1:1234/v1"))
        self.assertTrue(is_local_endpoint("HTTP://LOCALHOST:11434/v1"))
        self.assertFalse(is_local_endpoint("https://api.deepseek.com/v1"))
        self.assertFalse(is_local_endpoint("https://open.bigmodel.cn/api/paas/v4/"))
        self.assertFalse(is_local_endpoint(None))

    def test_local_endpoint_works_without_key(self):
        opt = SimpleNamespace(
            llm_api_key="", llm_base_url="http://localhost:11434/v1",
            llm_model="qwen2.5:7b", llm_system_prompt="系统",
        )
        svc = LLMService(opt)
        svc._ensure_api_key()  # 不应抛异常
        self.assertTrue(svc._local)

    def test_remote_endpoint_still_requires_key(self):
        opt = SimpleNamespace(
            llm_api_key="", llm_base_url="https://api.deepseek.com/v1",
            llm_model="deepseek-chat", llm_system_prompt="系统",
        )
        svc = LLMService(opt)
        with self.assertRaises(ValueError):
            svc._ensure_api_key()

    def test_remote_endpoint_with_key_ok(self):
        opt = SimpleNamespace(
            llm_api_key="sk-x", llm_base_url="https://open.bigmodel.cn/api/paas/v4/",
            llm_model="glm-4.7-flash", llm_system_prompt="系统",
        )
        svc = LLMService(opt)
        svc._ensure_api_key()  # 不应抛异常
        self.assertFalse(svc._local)


if __name__ == "__main__":
    unittest.main()
