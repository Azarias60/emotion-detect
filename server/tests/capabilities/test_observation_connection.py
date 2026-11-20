"""事实传输只检验订阅、发送与释放；不固定 SSE/WebSocket 等协议。"""
from types import SimpleNamespace
from unittest.mock import Mock

from server.tests.capabilities.bindings import capability
from server.tests.capabilities.conditions import Signal


def setup():
    signal = Signal([{"id": 31}])
    source = SimpleNamespace(observe=Mock(side_effect=lambda owner, listener: signal.observe(listener)))
    send = Mock()
    connection = capability("observation", owner_id=1, source=source, send=send)
    return connection, source, signal, send


def test_connection_subscribes_in_account_scope_and_sends_current_facts():
    connection, source, _, send = setup()
    connection.open()
    assert source.observe.call_args.args[0] == 1
    send.assert_called_with([{"id": 31}])
    connection.close()


def test_source_change_is_forwarded_without_a_command_or_focus_event():
    connection, _, signal, send = setup()
    connection.open()
    signal.publish([{"id": 31, "value": 72}])
    send.assert_called_with([{"id": 31, "value": 72}])
    connection.close()


def test_connection_end_releases_subscription_and_stops_delivery_only():
    connection, _, signal, send = setup()
    connection.open()
    connection.close()
    count = send.call_count
    signal.publish([])
    assert send.call_count == count
    assert not signal.listeners
