# -*-coding: utf-8 -*-
"""事实传输：报告能力的订阅、发送与连接释放。

连接只拥有订阅与投递：open 时取得当前账号范围的事实并发送，随后转发
来源变化；close 只解除订阅、停止投递，不改变报告、采集或后台处理的
使用期。传输协议不固定业务语义，业务能力不依赖此模块。
"""
import asyncio
import json

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse


class ObservationConnection:
    """一条事实订阅连接；send 由传输装配提供（跨线程投递自行封送）。"""

    def __init__(self, owner_id, source, send):
        self._owner_id = owner_id
        self._source = source
        self._send = send
        self._stop = None

    def open(self):
        if self._stop is not None:
            return
        self._stop = self._source.observe(self._owner_id, self._send)

    def close(self):
        if self._stop is None:
            return
        stop, self._stop = self._stop, None
        stop()


def create_observation_connection(owner_id, source, send) -> ObservationConnection:
    return ObservationConnection(owner_id, source, send)


def create_observation_router(current_principal, source) -> APIRouter:
    """报告事实流：账号范围订阅经 SSE 推送；认证依赖在装配时注入。"""
    router = APIRouter(tags=["事实传输"])

    @router.get("/observations/reports")
    async def report_stream(user=Depends(current_principal)):
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue()

        def forward(value):
            # 报告写入可能来自线程池线程；跨线程投递统一封送到事件循环。
            loop.call_soon_threadsafe(queue.put_nowait, value)

        connection = create_observation_connection(user.user_id, source, forward)

        async def stream():
            connection.open()
            try:
                while True:
                    facts = await queue.get()
                    yield f"data: {json.dumps(facts, ensure_ascii=False)}\n\n"
            finally:
                connection.close()

        return StreamingResponse(
            stream(), media_type="text/event-stream",
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        )

    return router


__all__ = ["ObservationConnection", "create_observation_connection", "create_observation_router"]
