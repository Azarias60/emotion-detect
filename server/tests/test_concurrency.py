"""Real workers must finish before their temporary files and locks are released."""
import asyncio
import threading
from contextvars import ContextVar
from tempfile import TemporaryDirectory
from pathlib import Path

import pytest

from server.concurrency import gather_and_cancel_siblings, run_blocking


@pytest.mark.parametrize("failure", [False, True], ids=["cancel", "sibling-failure"])
def test_cleanup_waits_for_real_thread_even_after_repeated_cancel(tmp_path, failure):
    ready, release = threading.Event(), threading.Event()
    lock = threading.Lock()
    observed = []
    directories = []

    def worker(path):
        ready.set()
        assert release.wait(3)
        observed.append((Path(path).read_text(), lock.locked()))

    async def scenario():
        async def fail():
            while not ready.is_set():
                await asyncio.sleep(0.001)
            raise ValueError("sibling failed")

        async def operation():
            with lock, TemporaryDirectory(dir=tmp_path) as directory:
                directories.append(directory)
                path = Path(directory) / "media.txt"
                path.write_text("alive")
                tasks = [asyncio.create_task(run_blocking(worker, str(path)))]
                if failure:
                    tasks.append(asyncio.create_task(fail()))
                await gather_and_cancel_siblings(*tasks)

        task = asyncio.create_task(operation())
        try:
            for _ in range(1000):
                if ready.is_set():
                    break
                await asyncio.sleep(0.001)
            assert ready.is_set()
            if not failure:
                task.cancel()
            await asyncio.sleep(0.02)
            assert not task.done()
            assert lock.locked()
            assert Path(directories[0]).exists()
            if not failure:
                task.cancel()
                await asyncio.sleep(0)
                assert not task.done()
            release.set()
            with pytest.raises(ValueError if failure else asyncio.CancelledError):
                await task
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())
    assert observed == [("alive", True)]
    assert not lock.locked()
    assert not Path(directories[0]).exists()


def test_blocking_worker_copies_context_without_blocking_loop():
    value = ContextVar("session", default=None)
    ready, release = threading.Event(), threading.Event()

    def worker():
        ready.set()
        assert release.wait(3)
        return value.get()

    async def scenario():
        value.set("session-1")
        task = asyncio.create_task(run_blocking(worker))
        try:
            for _ in range(1000):
                if ready.is_set():
                    break
                await asyncio.sleep(0.001)
            assert ready.is_set(), "event loop must progress while worker waits"
            release.set()
            assert await task == "session-1"
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())
