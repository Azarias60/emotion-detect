"""HTTP ``/human`` 路径的"新一轮取代上一轮"语义。

实时口型页的文字输入框（``web/app.js`` 的 ``liveForm``）走的是
``POST /human`` ``{type: "chat"}``，**不带 interrupt**。原实现直接派发
``llm_response``，于是上一轮还在飞的回复和新一轮会同时往同一条 TTS 队列
灌段落 —— 听感正是"回复重复、一直在说、互相重叠"。

同时锁住：WebRTC 输出的段落通知不能在"推入队列"和"真正发送"两处各打一次。
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
import unittest
from types import SimpleNamespace

import numpy as np

from avatars.base_avatar import BaseAvatar
from server import routes
from server.session_manager import session_manager
from streamout.webrtc import WebRTCOutput


class FakeRouteSession(BaseAvatar):
    """最小会话：不跑 BaseAvatar.__init__（真实初始化要 opt/TTS/输出模块），
    只装它需要的那几个字段，于是 ``begin_new_turn`` / ``reply_token_is_current``
    跑的是**真实实现**，代际语义不会在测试里被简化掉。
    """

    def __init__(self) -> None:
        self._reply_epoch = 0
        self.flushed = 0
        self.cleared = 0
        self.played: list[tuple[str, int | None]] = []
        self.output = SimpleNamespace(clear_buffer=self._clear)

    def _clear(self) -> None:
        self.cleared += 1

    def flush_talk(self) -> None:
        super().flush_talk()
        self.flushed += 1

    def put_msg_txt(self, msg, datainfo=None) -> None:
        # 复刻真实实现：未显式带代际的调用按当前代际盖章（BaseAvatar.put_msg_txt），
        # 再复刻 base_tts 的过期判定：带旧代际的段落被丢弃
        info = dict(datainfo or {})
        info.setdefault("reply_epoch", self._reply_epoch)
        epoch = info["reply_epoch"]
        if epoch < self._reply_epoch:
            return
        self.played.append((msg, epoch))


class FakeRequest:
    def __init__(self, payload: dict, llm_response=None) -> None:
        self._payload = payload
        self.app = {"llm_response": llm_response}

    async def json(self) -> dict:
        return self._payload


class HumanChatSupersedeTests(unittest.IsolatedAsyncioTestCase):
    def _make_session(self, label: str) -> tuple[str, FakeRouteSession]:
        session_id = f"test-human-{label}-{time.monotonic_ns()}"
        session = FakeRouteSession()
        session_manager.add_session(session_id, session)
        self.addCleanup(session_manager.remove_session, session_id)
        return session_id, session

    async def _post(self, session_id: str, payload: dict, llm_response=None):
        request = FakeRequest({**payload, "sessionid": session_id}, llm_response)
        return await routes.human(request)

    async def _wait_for(self, predicate, timeout: float = 3.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return True
            await asyncio.sleep(0.01)
        return False

    async def test_chat_without_interrupt_still_supersedes(self):
        """前端没带 interrupt 也必须打断上一轮（这就是线上故障入口）。"""
        calls: list[tuple[str, dict]] = []
        done = threading.Event()

        def fake_llm(text, session, datainfo, **kwargs):
            calls.append((text, kwargs))
            done.set()

        session_id, session = self._make_session("chat")
        response = await self._post(
            session_id, {"type": "chat", "text": "你好"}, fake_llm
        )
        self.assertEqual(0, json.loads(response.text)["code"])
        self.assertTrue(await self._wait_for(done.is_set), "chat 应派发 llm_response")

        self.assertEqual(1, session.flushed, "新一轮 chat 必须先打断上一轮")
        self.assertEqual(1, session.cleared, "在播语音的队列要清掉")
        self.assertEqual(1, session._reply_epoch)

        text, kwargs = calls[0]
        self.assertEqual("你好", text)
        should_stop = kwargs["should_stop"]
        self.assertFalse(should_stop())
        session.flush_talk()  # 下一轮到来
        self.assertTrue(should_stop(), "被取代的回复必须停手")

    async def test_second_chat_turn_stops_the_first_reply(self):
        gates: list = []

        def fake_llm(text, session, datainfo, **kwargs):
            gates.append(kwargs["should_stop"])

        session_id, session = self._make_session("twice")
        await self._post(session_id, {"type": "chat", "text": "第一句"}, fake_llm)
        self.assertTrue(await self._wait_for(lambda: len(gates) == 1))
        await self._post(session_id, {"type": "chat", "text": "第二句"}, fake_llm)
        self.assertTrue(await self._wait_for(lambda: len(gates) == 2))

        self.assertTrue(gates[0](), "上一轮回复必须被叫停，不能再往队列里灌")
        self.assertFalse(gates[1]())
        self.assertEqual(2, session.flushed)

    async def test_late_segments_of_the_old_reply_are_dropped(self):
        """旧回复在打断之后才送来的段落带旧代际，必须被 TTS 丢弃。"""
        session_id, session = self._make_session("late")
        await self._post(session_id, {"type": "chat", "text": "第一句"}, lambda *a, **k: None)
        old_epoch = session._reply_epoch
        await self._post(session_id, {"type": "chat", "text": "第二句"}, lambda *a, **k: None)

        session.put_msg_txt("旧回复的迟到段落。", {"reply_epoch": old_epoch})
        session.put_msg_txt("新回复的段落。", {"reply_epoch": session._reply_epoch})
        self.assertEqual([("新回复的段落。", 2)], session.played)

    async def test_echo_queues_without_interrupting(self):
        """echo 是纯朗读，不显式打断就该排队（不改变原有语义）。"""
        session_id, session = self._make_session("echo")
        response = await self._post(session_id, {"type": "echo", "text": "念这句"})
        self.assertEqual(0, json.loads(response.text)["code"])
        self.assertEqual(0, session.flushed)
        self.assertEqual(0, session.cleared)
        self.assertEqual([("念这句", 0)], session.played)


class BeginNewTurnTests(unittest.TestCase):
    """``BaseAvatar.begin_new_turn`` 本身的代际语义。"""

    def _bare_avatar(self) -> BaseAvatar:
        avatar = BaseAvatar.__new__(BaseAvatar)
        avatar._reply_epoch = 0
        return avatar

    def test_advances_epoch_and_gates_the_previous_reply(self):
        avatar = self._bare_avatar()
        should_stop = avatar.begin_new_turn()
        self.assertEqual(1, avatar._reply_epoch)
        self.assertFalse(should_stop())
        avatar.flush_talk()
        self.assertTrue(should_stop())


class WebRTCOutputNotifyTests(unittest.TestCase):
    def test_audio_notification_is_not_duplicated_on_push(self):
        class FakePlayer:
            def __init__(self) -> None:
                self.pushed = 0

            def push_audio(self, frame, eventpoint=None) -> None:
                self.pushed += 1

        class FakeAvatar:
            def __init__(self) -> None:
                self.notified = 0

            def notify(self, eventpoint) -> None:
                self.notified += 1

        output = WebRTCOutput.__new__(WebRTCOutput)
        output.parent = FakeAvatar()
        output._player = FakePlayer()
        output.push_audio_frame(np.zeros(320, dtype=np.int16), {"status": "start"})

        self.assertEqual(1, output._player.pushed)
        self.assertEqual(
            0,
            output.parent.notified,
            "推入队列时不得通知；通知只应发生在帧真正发给浏览器的那一刻",
        )


if __name__ == "__main__":
    unittest.main()
