"""控制入口不依赖当前输出模式；命令确认与重连回归。"""
import socket
import sys
import threading
from pathlib import Path
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from mcu.client import MCUClient
from mcu.control import MODE_COMMANDS, parse_mode_confirmation
from mcu.transport import SerialTransport, WiFiTransport
from test_mcu_protocol import MockTransport


def test_wifi_can_activate_board_in_serial_mode():
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        received = []
        finished = threading.Event()

        def firmware():
            with server.accept()[0] as peer:
                peer.settimeout(2)
                command = bytearray()
                while not command.endswith(b"\n"):
                    command.extend(peer.recv(32))
                received.append(bytes(command))
                # 固件当前为 serial，没有 WiFi 采集数据，但命令入口可用。
                peer.sendall(b"\x00\x04\x10\x01\x02\x00")
                finished.wait(2)

        worker = threading.Thread(target=firmware, daemon=True)
        worker.start()
        transport = WiFiTransport("127.0.0.1", server.getsockname()[1], 0)
        client = MCUClient(transport)
        try:
            client._receive_once()
            assert received == [MODE_COMMANDS["wifi"]]
            assert client.get_transport_info()["output_mode"] == "wifi"
            assert client.get_transport_info()["output_mode_status"] == "confirmed"
        finally:
            finished.set()
            client.stop()
            worker.join(3)


def test_usb_command_works_without_usb_capture_data():
    transport = SerialTransport("unused", 2000000)
    port = Mock()
    port.write.return_value = len(MODE_COMMANDS["serial"])
    transport._ser = port
    assert transport.request_output_mode() == "serial"
    port.write.assert_called_once_with(b"MODE SERIAL\n")


def test_no_ack_is_not_reported_as_success_and_reconnect_resends(monkeypatch):
    transport = MockTransport()
    transport.request_output_mode = Mock(return_value="serial")
    client = MCUClient(transport)
    monkeypatch.setattr("mcu.client.time.monotonic", lambda: 100)
    client._receive_once()
    assert client.get_transport_info()["output_mode_status"] == "pending"
    monkeypatch.setattr("mcu.client.time.monotonic", lambda: 104)
    assert client.get_transport_info()["output_mode_status"] == "unconfirmed"
    client._handle_decoded_packet(b"\x10\x01\x02")  # 错误模式不算成功
    assert client.get_transport_info()["output_mode_status"] == "unconfirmed"
    client._handle_decoded_packet(b"\x10\x01\x01")
    assert client.get_transport_info()["output_mode_status"] == "confirmed"
    transport.close()
    client._receive_once()
    assert transport.request_output_mode.call_count == 2
    assert client.get_transport_info()["output_mode_status"] == "pending"


def test_malformed_or_unknown_control_ack_is_rejected():
    for packet in [b"", b"\x10\x01", b"\x10\x02\x01", b"\x10\x01\x03", b"\x10\x01\x01x"]:
        assert parse_mode_confirmation(packet) is None
