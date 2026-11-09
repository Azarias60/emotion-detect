"""/api/readiness 预热探针：状态透传与放行规则。

全部离线（不加载模型），cosyvoice3 的预热状态直接打桩。
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest import mock

from aiohttp import web
from aiohttp.test_utils import AioHTTPTestCase

from server.browser_app import setup_browser_routes
from tts import cosyvoice3


def make_opt(tts: str = "cosyvoice3", default_tts: str = "cosyvoice3"):
    return SimpleNamespace(
        avatar_id="my_avatar",
        tts=tts,
        default_tts=default_tts,
    )


class CosyVoiceReadinessTests(AioHTTPTestCase):
    async def get_application(self) -> web.Application:
        app = web.Application()
        setup_browser_routes(app, make_opt())
        return app

    async def get(self) -> dict:
        resp = await self.client.get("/api/readiness")
        self.assertEqual(resp.status, 200)
        return await resp.json()

    async def test_loading_blocks(self):
        with mock.patch.object(cosyvoice3, "_warm_state", "loading"), \
             mock.patch.object(cosyvoice3, "_warm_error", ""):
            data = await self.get()
        self.assertFalse(data["ready"])
        self.assertEqual(data["tts"], "loading")

    async def test_ready_passes(self):
        with mock.patch.object(cosyvoice3, "_warm_state", "ready"), \
             mock.patch.object(cosyvoice3, "_warm_error", ""):
            data = await self.get()
        self.assertTrue(data["ready"])
        self.assertEqual(data["tts"], "ready")

    async def test_failed_still_passes_with_error(self):
        # 预热失败也放行：模型往往仍可用，别把用户锁死在加载页
        with mock.patch.object(cosyvoice3, "_warm_state", "failed"), \
             mock.patch.object(cosyvoice3, "_warm_error", "boom"):
            data = await self.get()
        self.assertTrue(data["ready"])
        self.assertEqual(data["tts"], "failed")
        self.assertEqual(data["error"], "boom")


class NonCosyVoiceReadinessTests(AioHTTPTestCase):
    async def get_application(self) -> web.Application:
        app = web.Application()
        setup_browser_routes(app, make_opt(tts="edge", default_tts="edge"))
        return app

    async def test_edge_engine_ready_immediately(self):
        # edge 引擎没有预热阶段，应直接就绪
        resp = await self.client.get("/api/readiness")
        self.assertEqual(resp.status, 200)
        data = await resp.json()
        self.assertTrue(data["ready"])
        self.assertEqual(data["tts"], "ready")


if __name__ == "__main__":
    unittest.main()
