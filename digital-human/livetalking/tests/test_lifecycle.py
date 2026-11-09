"""网页生命周期：心跳/告别判定与 /api/heartbeat、/api/bye 路由。

全部离线，不启看门狗线程、不加载模型。
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest import mock

from aiohttp import web
from aiohttp.test_utils import AioHTTPTestCase

from server import lifecycle
from server.browser_app import setup_browser_routes
from tts import cosyvoice3


def make_opt():
    return SimpleNamespace(avatar_id="my_avatar", tts="cosyvoice3", default_tts="cosyvoice3")


class LifecycleLogicTests(unittest.TestCase):
    def setUp(self):
        lifecycle._reset_for_tests()

    def test_fresh_server_never_exits_without_page(self):
        # 没有页面打开过就不退出（浏览器没弹出来也不许自杀）
        self.assertFalse(lifecycle.should_exit(now=lifecycle.epoch() + 10_000))

    def test_heartbeat_within_idle_keeps_alive(self):
        lifecycle.mark_seen()
        self.assertFalse(lifecycle.should_exit(now=lifecycle._last_seen + 10))

    def test_heartbeat_gap_beyond_idle_exits(self):
        # 后台标签页被浏览器限频到约 1 次/分钟，阈值 120s 仍能撑住；
        # 超过阈值（页面已关/崩溃）才退出
        lifecycle.mark_seen()
        self.assertTrue(lifecycle.should_exit(now=lifecycle._last_seen + lifecycle._IDLE_SEC + 1))

    def test_bye_exits_immediately(self):
        lifecycle.mark_seen()
        lifecycle.mark_bye()
        self.assertTrue(lifecycle.should_exit(now=lifecycle._last_seen + 0.1))

    def test_bye_alone_counts_as_seen(self):
        # "打开立刻关"：bye 先于任何心跳到达也要退出
        lifecycle.mark_bye()
        self.assertTrue(lifecycle.should_exit(now=lifecycle.epoch() + 0.1))


class LifecycleRouteTests(AioHTTPTestCase):
    async def get_application(self) -> web.Application:
        app = web.Application()
        setup_browser_routes(app, make_opt())
        return app

    def setUp(self):
        super().setUp()
        lifecycle._reset_for_tests()

    async def test_readiness_carries_epoch(self):
        with mock.patch.object(cosyvoice3, "_warm_state", "ready"), \
             mock.patch.object(cosyvoice3, "_warm_error", ""):
            resp = await self.client.get("/api/readiness")
        data = await resp.json()
        self.assertEqual(data["epoch"], lifecycle.epoch())

    async def test_heartbeat_marks_seen_and_returns_epoch(self):
        self.assertFalse(lifecycle._seen)
        resp = await self.client.post("/api/heartbeat")
        data = await resp.json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["epoch"], lifecycle.epoch())
        self.assertTrue(lifecycle._seen)

    async def test_bye_with_current_epoch_marks_exit(self):
        await self.client.post("/api/heartbeat")
        resp = await self.client.post(
            "/api/bye", data=f'{{"epoch": {lifecycle.epoch()}}}'
        )
        self.assertTrue((await resp.json())["ok"])
        self.assertTrue(lifecycle.should_exit())

    async def test_bye_with_stale_epoch_is_ignored(self):
        # 上一代页面的告别不能误杀新拉起的服务
        await self.client.post("/api/heartbeat")
        resp = await self.client.post(
            "/api/bye", data=f'{{"epoch": {lifecycle.epoch() - 999}}}'
        )
        self.assertFalse((await resp.json())["ok"])
        self.assertFalse(lifecycle.should_exit())

    async def test_bye_without_payload_still_marks_exit(self):
        # sendBeacon 允许空正文（老版本页面），按有效告别处理
        await self.client.post("/api/heartbeat")
        resp = await self.client.post("/api/bye", data="")
        self.assertTrue((await resp.json())["ok"])
        self.assertTrue(lifecycle.should_exit())


if __name__ == "__main__":
    unittest.main()
