# -*-coding: utf-8 -*-
"""单片机（MCU）协议解析层、设备客户端与会话管理单元测试。"""
import os
import sys
import struct
import tempfile
import cv2
import numpy as np
from cobs import cobs

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mcu.protocol import (
    AudioChunk,
    HeartRateReading,
    MCUProtocolParser,
    PacketType,
    TemperatureReading,
    VideoFrame,
)
from mcu.client import MCUClient
from mcu.transport import BaseTransport
from mcu import mcu_client


class MockTransport(BaseTransport):
    """用于单元测试的模拟通信传输。"""

    def __init__(self):
        self._is_open = False
        self._incoming = bytearray()

    def open(self) -> bool:
        self._is_open = True
        return True

    def close(self):
        self._is_open = False

    def is_open(self) -> bool:
        return self._is_open

    def push_bytes(self, data: bytes):
        self._incoming.extend(data)

    def read(self, max_bytes: int = 4096) -> bytes:
        if not self._is_open or not self._incoming:
            return b""
        chunk = bytes(self._incoming[:max_bytes])
        del self._incoming[:max_bytes]
        return chunk

    def reset_buffers(self):
        self._incoming.clear()


def test_protocol_parser_isolated():
    """测试协议分帧与解析模块（纯协议测试，不依赖 I/O）。"""
    parser = MCUProtocolParser()

    # 1. 测试音频包解析 (Type 0x01: 48kHz mono int16)
    pcm_raw = (np.sin(np.linspace(0, 100, 256)) * 10000).astype(np.int16).tobytes()
    raw_packet = bytes([PacketType.AUDIO]) + pcm_raw
    cobs_frame = cobs.encode(raw_packet) + b"\x00"

    # 分两次 feed 模拟网络/串口分片
    packets = parser.feed_bytes(cobs_frame[:10])
    assert len(packets) == 0
    packets = parser.feed_bytes(cobs_frame[10:])
    assert len(packets) == 1
    decoded = packets[0]

    pkg_type, parsed = parser.parse_packet(decoded)
    assert pkg_type == PacketType.AUDIO
    assert isinstance(parsed, AudioChunk)
    assert parsed.raw_pcm == pcm_raw
    assert parsed.volume > 0
    assert len(parsed.wave) > 0

    # 2. 测试温度包解析 (Type 0x02: <ff)
    temp_raw = struct.pack("<Bff", PacketType.TEMPERATURE, 24.5, 36.6)
    cobs_temp = cobs.encode(temp_raw) + b"\x00"
    decoded_temp = parser.feed_bytes(cobs_temp)[0]
    pkg_type, parsed_temp = parser.parse_packet(decoded_temp)
    assert pkg_type == PacketType.TEMPERATURE
    assert isinstance(parsed_temp, TemperatureReading)
    assert parsed_temp.ambient == 24.5
    assert parsed_temp.object_temp == 36.6

    # 3. 测试心率包解析 (Type 0x04: <f)
    hr_raw = struct.pack("<Bf", PacketType.HEART_RATE, 75.5)
    cobs_hr = cobs.encode(hr_raw) + b"\x00"
    decoded_hr = parser.feed_bytes(cobs_hr)[0]
    pkg_type, parsed_hr = parser.parse_packet(decoded_hr)
    assert pkg_type == PacketType.HEART_RATE
    assert isinstance(parsed_hr, HeartRateReading)
    assert parsed_hr.bpm == 75.5

    # 4. 测试图像包解析 (Type 0xFF: JPEG 旋转与镜像)
    dummy_img = np.zeros((80, 120, 3), dtype=np.uint8)
    _, jpeg_buf = cv2.imencode(".jpg", dummy_img)
    jpeg_bytes = jpeg_buf.tobytes()
    cobs_cam = cobs.encode(jpeg_bytes) + b"\x00"
    decoded_cam = parser.feed_bytes(cobs_cam)[0]
    pkg_type, parsed_cam = parser.parse_packet(decoded_cam)
    assert pkg_type == PacketType.CAMERA
    assert isinstance(parsed_cam, VideoFrame)
    # 原图 80 高 120 宽，逆时针转 90 度后变为 120 高 80 宽
    assert parsed_cam.image.shape == (120, 80, 3)

    print("✅ 协议解析层 (MCUProtocolParser) 纯逻辑测试全部通过！")


def test_mcu_client_session_and_encapsulation():
    """测试 MCUClient 会话管理与指标计算。"""
    mock_transport = MockTransport()
    client = MCUClient(transport=mock_transport)

    # 1. 会话录制开启
    client.start_recording(duration=60, capture_video=True)
    assert client.is_recording is True
    assert client.has_heart_rate_data() is False
    assert client.has_recorded_video() is False

    # 2. 构造多模态数据包直接交付给 client 内部处理
    # 心率序列
    for hr in [70.0, 72.0, 74.0, 76.0, 78.0]:
        hr_data = struct.pack("<Bf", PacketType.HEART_RATE, hr)
        client._handle_decoded_packet(hr_data)

    # 温度数据
    temp_data = struct.pack("<Bff", PacketType.TEMPERATURE, 25.0, 36.5)
    client._handle_decoded_packet(temp_data)

    # 音频分块
    dummy_pcm = b"\x00\x01" * 256
    audio_data = bytes([PacketType.AUDIO]) + dummy_pcm
    client._handle_decoded_packet(audio_data)

    # 图像数据
    dummy_img = np.zeros((100, 100, 3), dtype=np.uint8)
    _, jpeg_buf = cv2.imencode(".jpg", dummy_img)
    client._handle_decoded_packet(jpeg_buf.tobytes())

    # 3. 校验封装方法业务数据
    assert client.has_heart_rate_data() is True
    assert client.has_recorded_video() is True
    assert client.has_video_signal() is True

    hr_summary = client.get_heart_rate_summary()
    assert hr_summary["avg_hr"] == 74.0
    assert hr_summary["latest_hr"] == 78.0
    assert "SDNN" in hr_summary["hrv_indices"]

    temp_summary = client.get_temperature_summary()
    assert temp_summary["avg_ambient_temp"] == 25.0
    assert temp_summary["avg_object_temp"] == 36.5

    video_summary = client.get_video_summary()
    assert video_summary["recorded_frames_count"] == 1
    assert video_summary["has_signal"] is True

    status_data = client.get_device_status()
    assert "heart_rate" in status_data
    assert "temperature" in status_data
    assert "audio" in status_data
    assert "video" in status_data

    # 4. 测试音频保存
    with tempfile.TemporaryDirectory() as tmpdir:
        audio_files = client.save_recorded_audio(tmpdir, "test_client_audio")
        assert audio_files["raw_wav"] is not None
        assert os.path.exists(audio_files["raw_wav"])

    # 5. 停止录制
    client.stop_recording()
    assert client.is_recording is False

    print("✅ MCUClient 纯封装与设备管理测试全部通过！")


def test_chart_snapshots_only_include_current_recording(monkeypatch):
    client = MCUClient(transport=MockTransport())
    now = [100.0]
    monkeypatch.setattr("mcu.client.time.monotonic", lambda: now[0])

    def send_audio(amplitude):
        pcm = np.full(256, amplitude, dtype="<i2").tobytes()
        client._handle_decoded_packet(bytes([PacketType.AUDIO]) + pcm)

    def send_hr(bpm):
        client._handle_decoded_packet(struct.pack("<Bf", PacketType.HEART_RATE, bpm))

    send_audio(16000)
    send_hr(90)
    client.start_recording(duration=60)
    assert client.get_audio_summary()["recorded_wave"] == []
    assert client.get_heart_rate_summary()["hr_list"] == []

    send_audio(-16000)
    send_hr(72)
    recorded = client.get_audio_summary()["recorded_wave"]
    assert len(recorded) == 32 and all(value < 0 for value in recorded)
    client.stop_recording()
    send_audio(8000)
    send_hr(100)
    assert client.get_audio_summary()["recorded_wave"] == recorded
    assert client.get_heart_rate_summary()["hr_list"] == [72.0]
    client._clear_live_data()
    assert client.get_audio_summary()["wave"] == []
    assert client.get_audio_summary()["recorded_wave"] == recorded

    client.start_recording(duration=1)
    assert client.get_audio_summary()["recorded_wave"] == []
    assert client.get_heart_rate_summary()["hr_list"] == []
    send_audio(0)
    assert client.get_audio_summary()["recorded_wave"] == [0.0] * 32
    now[0] += 2
    send_audio(16000)
    send_hr(80)
    assert client.get_audio_summary()["recorded_wave"] == [0.0] * 32
    assert client.get_heart_rate_summary()["hr_list"] == []


def test_live_monitoring_continues_outside_recording_and_preserves_session():
    client = MCUClient(transport=MockTransport())

    def send_sample(hr, ambient, body):
        client._handle_decoded_packet(struct.pack("<Bf", PacketType.HEART_RATE, hr))
        client._handle_decoded_packet(struct.pack("<Bff", PacketType.TEMPERATURE, ambient, body))
        pcm = np.full(256, int(hr * 100), dtype="<i2").tobytes()
        client._handle_decoded_packet(bytes([PacketType.AUDIO]) + pcm)

    send_sample(70, 24, 35)
    assert client.get_heart_rate_summary()["live_hr_list"] == [70]
    assert client.get_heart_rate_summary()["hr_list"] == []
    assert client.get_audio_summary()["wave"]
    assert client.get_audio_summary()["chunks_count"] == 0

    client.start_recording()
    send_sample(72, 25, 36)
    client.stop_recording()
    recorded_wave = client.get_audio_summary()["recorded_wave"]
    send_sample(80, 26, 37)
    assert client.get_heart_rate_summary()["live_hr_list"] == [70, 72, 80]
    assert client.get_heart_rate_summary()["hr_list"] == [72]
    assert client.get_heart_rate_summary()["latest_hr"] == 80
    assert client.get_temperature_summary()["latest_object_temp"] == 37
    assert client.get_temperature_summary()["avg_object_temp"] == 36
    assert client.get_audio_summary()["recorded_wave"] == recorded_wave
    assert client.get_audio_summary()["wave"][-1] != recorded_wave[-1]

    for _ in range(150):
        send_sample(81, 26, 37)
    assert len(client.get_heart_rate_summary()["live_hr_list"]) == 128
    client._clear_live_data()
    assert client.get_heart_rate_summary()["live_hr_list"] == []
    assert client.get_heart_rate_summary()["latest_hr"] is None
    assert client.get_temperature_summary()["latest_object_temp"] is None
    assert client.get_heart_rate_summary()["hr_list"] == [72]
    client.start_recording()
    send_sample(85, 27, 38)
    assert client.get_heart_rate_summary()["live_hr_list"] == [85]


if __name__ == "__main__":
    test_protocol_parser_isolated()
    test_mcu_client_session_and_encapsulation()
    print("🎉 单片机独立模块纯净单元测试全部通过！")
