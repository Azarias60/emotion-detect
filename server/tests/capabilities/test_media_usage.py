"""媒体消费只管理自己的连接、聆听与播放，不拥有上下文或录音。"""
from types import SimpleNamespace
from unittest.mock import Mock

from server.tests.capabilities.bindings import capability
from server.tests.capabilities.conditions import Signal


def setup():
    recording = Signal(False)
    resources = Signal({"configuration": "c1", "avatar": "a1", "voice": "v1"})
    transport = SimpleNamespace(open=Mock(), close=Mock(), listen=Mock(), stop_playback=Mock())
    usage = capability("media", transport=transport, recording=recording, resources=resources)
    return usage, transport, recording


def test_usage_start_and_end_acquire_and_release_only_media():
    usage, transport, _ = setup()
    usage.set_usage(True)
    transport.open.assert_called()
    usage.set_usage(False)
    transport.listen.assert_called_with(False)
    transport.stop_playback.assert_called()
    transport.close.assert_called()


def test_recording_suppresses_listening_and_playback_without_replacing_user_intent():
    usage, transport, recording = setup()
    usage.set_usage(True)
    usage.set_listening(True)
    recording.publish(True)
    transport.listen.assert_called_with(False)
    transport.stop_playback.assert_called()
    recording.publish(False)
    transport.listen.assert_called_with(True)


def test_recording_end_does_not_enable_listening_when_user_intent_is_off():
    usage, transport, recording = setup()
    usage.set_usage(True)
    usage.set_listening(False)
    recording.publish(True)
    recording.publish(False)
    transport.listen.assert_called_with(False)


def test_ended_usage_unsubscribes_from_recording_conditions():
    usage, transport, recording = setup()
    usage.set_usage(True)
    usage.set_usage(False)
    before = list(transport.listen.call_args_list)
    recording.publish(True)
    recording.publish(False)
    assert transport.listen.call_args_list == before
    assert not recording.listeners


def test_effective_resource_change_stops_playback_and_updates_media_input():
    recording = Signal(False)
    resources = Signal({"configuration": "c1", "avatar": "a1", "voice": "v1"})
    transport = SimpleNamespace(open=Mock(), close=Mock(), listen=Mock(), stop_playback=Mock(),
                                set_resources=Mock())
    usage = capability("media", transport=transport, recording=recording, resources=resources)
    usage.set_usage(True)
    transport.stop_playback.reset_mock()
    resources.publish({"configuration": "c1", "avatar": "a1", "voice": "v2"})
    transport.stop_playback.assert_called()
    transport.set_resources.assert_called_with({"configuration": "c1", "avatar": "a1", "voice": "v2"})
    usage.set_usage(False)


def test_missing_configuration_ends_media_use_without_selecting_another_object():
    resources = Signal({"configuration": "c1", "avatar": "a1", "voice": "v1"})
    transport = SimpleNamespace(open=Mock(), close=Mock(), listen=Mock(), stop_playback=Mock(),
                                set_resources=Mock())
    usage = capability("media", transport=transport, recording=Signal(False), resources=resources)
    usage.set_usage(True)
    resources.publish({"configuration": None, "avatar": None, "voice": None})
    transport.close.assert_called()
    usage.set_usage(False)
