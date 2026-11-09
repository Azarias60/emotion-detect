"""以 mix.ino 实际发送格式验证协议边界，不需要硬件或模型。"""
import struct
import sys
from pathlib import Path

import cv2
import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.hardware_profile import MAX_ENCODED_FRAME_SIZE
from mcu.client import MCUClient
from mcu.protocol import MCUProtocolParser, PacketType
from mcu.transport import SerialTransport, WiFiTransport


def firmware_encode(payload):
    """逐步复现 mix.ino 编码器，包括末尾零未补空块的行为。"""
    output = bytearray()
    offset = 0
    while offset < len(payload):
        code_index = len(output)
        output.append(0)
        code = 1
        while offset < len(payload) and code < 255:
            if payload[offset] == 0:
                break
            output.append(payload[offset])
            offset += 1
            code += 1
        output[code_index] = code
        if code < 255 and offset < len(payload) and payload[offset] == 0:
            offset += 1
    return bytes(output) + b"\x00"


@pytest.mark.parametrize("payload", [
    b"\x01" + b"\x00" * 512,
    b"\x01" + b"\xff" * 511 + b"\x00",
    b"\x01" + b"\x00\x80" * 256,
    struct.pack("<Bff", 2, 24.5, 0.0),
    struct.pack("<Bf", 4, 0.0),
    struct.pack("<Bf", 4, 72.0),
])
def test_real_firmware_cobs_round_trip(payload):
    encoded = firmware_encode(payload)
    parser = MCUProtocolParser()
    packets = []
    for offset in range(0, len(encoded), 7):
        packets.extend(parser.feed_bytes(encoded[offset:offset + 7]))
    assert packets == [payload]


def test_strict_raw_packets_and_negative_full_scale():
    parser = MCUProtocolParser()
    for packet in [b"\x01" + bytes(511), b"\x01" + bytes(513),
                   struct.pack("<Bf", 4, 72) + b"x", b"\x04" + bytes(4),
                   struct.pack("<Bff", 2, float("nan"), 36)]:
        assert parser.parse_packet(packet) == (None, None)
    kind, audio = parser.parse_packet(b"\x01" + b"\x00\x80" * 256)
    assert kind == PacketType.AUDIO
    assert audio.volume == 100
    assert all(value >= 0 for value in audio.wave)


def test_oversized_stream_recovers_at_next_delimiter():
    parser = MCUProtocolParser()
    assert parser.feed_bytes(b"a" * (MAX_ENCODED_FRAME_SIZE + 1)) == []
    payload = struct.pack("<Bf", 4, 72)
    assert parser.feed_bytes(b"discard\x00" + firmware_encode(payload)) == [payload]


def test_radar_hrv_is_missing_and_recording_expires_offline(monkeypatch):
    monkeypatch.setattr("mcu.client.time.monotonic", lambda: 100)
    client = MCUClient()
    client.start_recording(duration=1)
    client._handle_decoded_packet(struct.pack("<Bf", 4, 72))
    assert all(value is None for value in client.get_heart_rate_summary()["hrv_indices"].values())
    monkeypatch.setattr("mcu.client.time.monotonic", lambda: 102)
    client._handle_decoded_packet(struct.pack("<Bf", 4, 80))
    assert not client.is_recording
    assert client.get_heart_rate_summary()["hr_list"] == [72]


def test_video_timing_and_transport_switch_clear_live_frame(monkeypatch):
    client = MCUClient()
    clock = iter([100, 100, 100, 100.2, 100.2])
    monkeypatch.setattr("mcu.client.time.monotonic", lambda: next(clock))
    client.start_recording()
    _, jpeg = cv2.imencode(".jpg", np.zeros((10, 10, 3), np.uint8))
    client._handle_decoded_packet(jpeg.tobytes())
    client._handle_decoded_packet(jpeg.tobytes())
    assert client.get_recorded_fps() == pytest.approx(5)
    client.switch_transport(SerialTransport("unused", 115200))
    assert not client.has_video_signal()
    assert client.has_recorded_video()
    assert not client.is_recording


def test_failed_tcp_connect_releases_udp_binding():
    import socket
    with socket.socket() as refused:
        refused.bind(("127.0.0.1", 0))
        transport = WiFiTransport("127.0.0.1", refused.getsockname()[1], 0, connect_timeout=0.1)
        assert not transport.open()
        assert transport._tcp_sock is None
        assert transport._udp_sock is None


def test_reconnect_drops_partial_frame():
    from test_mcu_protocol import MockTransport
    transport = MockTransport()
    client = MCUClient(transport)
    transport.open()
    first = firmware_encode(struct.pack("<Bf", 4, 72))
    transport.push_bytes(first[:3])
    client._receive_once()
    transport.close()
    transport.push_bytes(firmware_encode(struct.pack("<Bf", 4, 84)))
    client._receive_once()
    assert client.get_heart_rate_summary()["latest_hr"] == 84


def test_udp_rejects_other_senders_and_incorrect_lengths():
    transport = WiFiTransport()
    transport._peer_ip = "192.0.2.1"
    good = b"\x01" + bytes(512)
    incoming = iter([
        (good, ("192.0.2.2", 1000)),
        (good[:-1], ("192.0.2.1", 1000)),
        (good, ("192.0.2.1", 23456)),
    ])

    class DatagramSocket:
        def recvfrom(self, size):
            try:
                return next(incoming)
            except StopIteration:
                transport._stop_event.set()
                raise OSError("finished")

    transport._udp_sock = DatagramSocket()
    transport._udp_listener_loop()
    assert transport.read_packets() == [good]
