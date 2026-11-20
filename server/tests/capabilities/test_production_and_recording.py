"""提交任务与未提交录音分别测试；不调用真实设备或生成模型。"""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, sentinel

import pytest

from server.tests.capabilities.bindings import capability
from server.tests.capabilities.conditions import settle


@pytest.mark.parametrize("kind", ["avatar", "voice"])
def test_submitted_task_keeps_its_input_owner_and_delivers_only_its_product(kind):
    async def case():
        release = asyncio.Event()
        async def generate(value):
            assert value is sentinel.input
            await release.wait()
            return sentinel.product
        produce = AsyncMock(side_effect=generate)
        output = SimpleNamespace(create=Mock())
        task = capability("task", owner_id=1, kind=kind, input=sentinel.input,
                          produce=produce, output=output)
        await task.start()
        await settle()
        # 页面和登录不是已提交任务的输入；不模拟由它们驱动任务继续。
        output.create.assert_not_called()
        release.set()
        await task.wait()
        output.create.assert_called_once_with(1, sentinel.product)
        produce.assert_awaited_once_with(sentinel.input)
    asyncio.run(case())


def test_explicit_task_cancel_stops_execution_without_creating_product():
    async def case():
        cancelled = asyncio.Event()
        async def produce(value):
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()
        output = SimpleNamespace(create=Mock())
        task = capability("task", owner_id=1, kind="voice", input=sentinel.input,
                          produce=produce, output=output)
        await task.start()
        await settle()
        await task.cancel()
        assert cancelled.is_set()
        output.create.assert_not_called()
    asyncio.run(case())


def setup_recording():
    audio = SimpleNamespace(start=Mock(return_value=sentinel.recording),
                            finish=Mock(return_value=sentinel.audio), cancel=Mock())
    submit = Mock(return_value=sentinel.task)
    recording = capability("recording", audio=audio, submit=submit)
    return recording, audio, submit


def test_unsubmitted_recording_is_cancelled_when_usage_ends():
    recording, audio, submit = setup_recording()
    recording.set_usage(True)
    recording.start()
    audio.start.assert_called_once()
    recording.set_usage(False)
    audio.cancel.assert_called_with(sentinel.recording)
    submit.assert_not_called()


def test_stop_and_submit_hands_audio_to_task_without_owning_task_lifetime():
    recording, audio, submit = setup_recording()
    recording.set_usage(True)
    recording.start()
    task = recording.stop_and_submit()
    audio.finish.assert_called_with(sentinel.recording)
    submit.assert_called_once_with(sentinel.audio)
    assert task is sentinel.task
    recording.set_usage(False)
    audio.cancel.assert_not_called()


def test_explicit_recording_cancel_does_not_submit_audio():
    recording, audio, submit = setup_recording()
    recording.set_usage(True)
    recording.start()
    recording.cancel()
    audio.cancel.assert_called_with(sentinel.recording)
    submit.assert_not_called()
