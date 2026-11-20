"""Bounded blocking work, task admission and cancellation that preserve resource ownership."""
import asyncio
import contextvars
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import AbstractContextManager, contextmanager
from functools import partial
from typing import Iterator, Protocol

from server import settings
from server.detection_errors import DetectionBusyError

_executor = ThreadPoolExecutor(
    max_workers=settings.ANALYSIS_WORKERS,
    thread_name_prefix="emotion-analysis",
)


class TaskAdmission:
    """全局容量准入（默认 1）；``acquire`` 语义与既有忙响应一致。

    一次执行只准入一次：容量内进入并持有至实际工作结束，超限立即抛出
    ``DetectionBusyError``（消息由调用方按操作提供），不排队、不创建临时
    目录。进程内实现；生产如需多实例准入，可注入同等契约的外部实现。
    """

    def __init__(self, capacity: int = 1):
        self.capacity = max(1, int(capacity))
        self._active = 0
        self._lock = threading.Lock()

    @contextmanager
    def acquire(
        self,
        key: str = "task",
        busy_message: str = "当前分析服务繁忙，请稍后重试",
    ) -> Iterator[None]:
        with self._lock:
            if self._active >= self.capacity:
                raise DetectionBusyError(busy_message)
            self._active += 1
        try:
            yield
        finally:
            with self._lock:
                self._active -= 1


class TaskAdmissionPort(Protocol):
    """任务准入契约；一次执行只准入一次，忙时立即拒绝。"""

    def acquire(
        self,
        key: str = "task",
        busy_message: str = "当前分析服务繁忙，请稍后重试",
    ) -> AbstractContextManager[None]: ...


async def _settle(awaitables):
    """Drain actual work, including when the caller receives another cancellation."""
    drain = asyncio.gather(*awaitables, return_exceptions=True)
    while not drain.done():
        try:
            await asyncio.shield(drain)
        except asyncio.CancelledError:
            continue
    return drain.result()


async def run_blocking(function, *args, **kwargs):
    """Keep files/locks alive until an already running worker really finishes."""
    context = contextvars.copy_context()
    future = asyncio.get_running_loop().run_in_executor(
        _executor, context.run, partial(function, *args, **kwargs),
    )
    try:
        return await asyncio.shield(future)
    except asyncio.CancelledError:
        await _settle([future])
        raise


async def gather_and_cancel_siblings(*tasks):
    """Cancel sibling coroutines and await their worker/resource cleanup."""
    try:
        return await asyncio.gather(*tasks)
    except BaseException:
        for task in tasks:
            if not task.done():
                task.cancel()
        await _settle(tasks)
        raise


__all__ = ["TaskAdmission", "TaskAdmissionPort", "gather_and_cancel_siblings", "run_blocking"]
