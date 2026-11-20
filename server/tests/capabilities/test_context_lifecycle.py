"""后端上下文只检查自己的存在条件、唤醒、重置及模型配置消费。"""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from server.tests.capabilities.bindings import capability


def setup(kind):
    resource = SimpleNamespace(reply=AsyncMock(return_value="reply"), clear=Mock(), close=Mock())
    engine = SimpleNamespace(open=Mock(return_value=resource))
    config = SimpleNamespace(read=Mock(return_value="key-a"))
    context = capability("context", owner_id=1, login_id="login-a", kind=kind,
                         engine=engine, model_config=config)
    return context, resource, engine, config


@pytest.mark.parametrize("kind", ["guide", "companion"])
def test_context_lifetime_is_decided_by_its_own_usage(kind):
    async def case():
        context, resource, engine, _ = setup(kind)
        await context.set_usage(True)
        await context.send("hello")
        await context.set_usage(False)
        if kind == "guide":
            resource.close.assert_called()
        else:
            resource.close.assert_not_called()
        await context.set_usage(True)
        assert engine.open.call_count == (2 if kind == "guide" else 1)
        await context.set_login(None)
        resource.close.assert_called()
    asyncio.run(case())


@pytest.mark.parametrize("kind", ["guide", "companion"])
def test_explicit_restart_clears_context_but_does_not_replace_its_owner(kind):
    async def case():
        context, resource, engine, _ = setup(kind)
        await context.set_usage(True)
        await context.send("previous")
        await context.restart()
        resource.clear.assert_called_once()
        assert engine.open.call_count == 1
        await context.hear("next")
        assert resource.reply.await_args.args[0] == "next"
    asyncio.run(case())


@pytest.mark.parametrize("kind", ["guide", "companion"])
def test_greeting_wakes_without_reset_and_following_speech_is_consumed(kind):
    async def case():
        context, resource, _, _ = setup(kind)
        await context.set_usage(True)
        await context.hear("待命时的声音")
        resource.reply.assert_not_awaited()
        await context.hear("你好")
        await context.hear("本次问题")
        assert resource.reply.await_args.args[0] == "本次问题"
        await context.hear("你好")
        resource.clear.assert_not_called()
    asyncio.run(case())


def test_next_turn_reads_current_account_model_configuration_without_resetting_context():
    async def case():
        context, resource, engine, config = setup("companion")
        await context.set_usage(True)
        await context.send("first")
        assert resource.reply.await_args.args == ("first", "key-a")
        config.read.return_value = "key-b"
        await context.send("second")
        config.read.assert_called_with(1)
        assert resource.reply.await_args.args == ("second", "key-b")
        resource.clear.assert_not_called()
        assert engine.open.call_count == 1
    asyncio.run(case())
