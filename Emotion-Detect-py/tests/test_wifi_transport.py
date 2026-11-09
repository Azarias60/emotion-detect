# -*-coding: utf-8 -*-
"""WiFi 传输层 (WiFiTransport) 与多通道多模态数据交互单元测试。"""
import os
import socket
import struct
import sys
import threading
import time
import cv2
import numpy as np
from cobs import cobs

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.config import WIFI_CONFIG
from mcu.client import MCUClient
from mcu.protocol import PacketType
from mcu.transport import SerialTransport, WiFiTransport


def get_free_port() -> int:
    """动态获取操作系统当前未占用的本地端口。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_wifi_transport_tcp_and_udp():
    """测试 WiFiTransport 的 TCP 帧流与 UDP 音频通道收发。"""
    tcp_port = get_free_port()
    udp_port = get_free_port()

    server_tcp = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_tcp.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server_tcp.bind(("127.0.0.1", tcp_port))
    server_tcp.listen(1)

    accepted_conn = []
    server_ready = threading.Event()

    def server_thread_func():
        server_ready.set()
        conn, _ = server_tcp.accept()
        accepted_conn.append(conn)

    th = threading.Thread(target=server_thread_func, daemon=True)
    th.start()
    server_ready.wait(2.0)

    # 1. 建立客户端传输
    transport = WiFiTransport(
        host="127.0.0.1",
        tcp_port=tcp_port,
        udp_port=udp_port,
        timeout=0.1,
        connect_timeout=2.0,
    )
    ok = transport.open()
    assert ok is True
    assert transport.is_open() is True
    assert transport.get_info()["type"] == "wifi"
    assert transport.get_info()["host"] == "127.0.0.1"

    th.join(timeout=2.0)
    assert len(accepted_conn) == 1
    conn = accepted_conn[0]

    try:
        # 2. 模拟 ESP32 通过 TCP 发送温度与心率 COBS 帧
        temp_data = struct.pack("<Bff", PacketType.TEMPERATURE, 23.5, 36.7)
        cobs_temp = cobs.encode(temp_data) + b"\x00"
        hr_data = struct.pack("<Bf", PacketType.HEART_RATE, 72.0)
        cobs_hr = cobs.encode(hr_data) + b"\x00"
        conn.sendall(cobs_temp + cobs_hr)

        # 客户端读取 TCP 流
        received = bytearray()
        start_t = time.time()
        while len(received) < len(cobs_temp) + len(cobs_hr) and time.time() - start_t < 2.0:
            chunk = transport.read(1024)
            if chunk:
                received.extend(chunk)
            else:
                time.sleep(0.01)

        assert bytes(received) == cobs_temp + cobs_hr

        # 3. 模拟 ESP32 通过 UDP 发送音频数据包 (0x01 + PCM)
        dummy_pcm = (np.sin(np.linspace(0, 10, 256)) * 5000).astype(np.int16).tobytes()
        udp_audio_pkt = bytes([PacketType.AUDIO]) + dummy_pcm

        udp_sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        # 损坏包不能作为有效 PCM 排队；后续合法包仍可正常读取。
        udp_sender.sendto(udp_audio_pkt[:-1], ("127.0.0.1", udp_port))
        udp_sender.sendto(udp_audio_pkt + b"x", ("127.0.0.1", udp_port))
        udp_sender.sendto(udp_audio_pkt, ("127.0.0.1", udp_port))
        udp_sender.close()

        # 客户端从 UDP 队列提取独立数据包
        packets = []
        start_t = time.time()
        while not packets and time.time() - start_t < 2.0:
            packets = transport.read_packets()
            if not packets:
                time.sleep(0.02)

        assert len(packets) >= 1
        assert packets[0] == udp_audio_pkt
        assert packets[0][0] == PacketType.AUDIO

    finally:
        transport.close()
        conn.close()
        server_tcp.close()

    assert transport.is_open() is False
    print("✅ WiFiTransport 双通道 TCP/UDP 传输测试全部通过！")


def test_mcu_client_with_wifi_transport():
    """测试 MCUClient 装配 WiFiTransport 进行多模态录制与指标采集。"""
    tcp_port = get_free_port()
    udp_port = get_free_port()

    server_tcp = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_tcp.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server_tcp.bind(("127.0.0.1", tcp_port))
    server_tcp.listen(1)

    accepted_conn = []

    def server_thread_func():
        try:
            conn, _ = server_tcp.accept()
            accepted_conn.append(conn)
        except Exception:
            pass

    th = threading.Thread(target=server_thread_func, daemon=True)
    th.start()

    transport = WiFiTransport(
        host="127.0.0.1",
        tcp_port=tcp_port,
        udp_port=udp_port,
        timeout=0.1,
    )
    client = MCUClient(transport=transport)
    client.start()

    try:
        # 等待后台工作线程连接成功
        start_t = time.time()
        while not client.is_connected and time.time() - start_t < 3.0:
            time.sleep(0.05)
        assert client.is_connected is True

        client.start_recording(duration=60, capture_video=True)
        assert client.is_recording is True

        conn = accepted_conn[0]

        # 1. 模拟 ESP32 发送心率、温度、摄像头图像
        temp_data = struct.pack("<Bff", PacketType.TEMPERATURE, 26.0, 36.8)
        hr_data = struct.pack("<Bf", PacketType.HEART_RATE, 80.0)

        dummy_img = np.zeros((60, 80, 3), dtype=np.uint8)
        _, jpeg_buf = cv2.imencode(".jpg", dummy_img)
        cam_frame = cobs.encode(jpeg_buf.tobytes()) + b"\x00"

        conn.sendall(cobs.encode(temp_data) + b"\x00")
        conn.sendall(cobs.encode(hr_data) + b"\x00")
        conn.sendall(cam_frame)

        # 2. 模拟 ESP32 发送音频 UDP 包
        udp_sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        audio_pkt = bytes([PacketType.AUDIO]) + (b"\x10\x20" * 256)
        udp_sender.sendto(audio_pkt, ("127.0.0.1", udp_port))

        # 验证 client 数据汇聚
        start_t = time.time()
        while (not client.has_heart_rate_data() or not client.has_video_signal()) and time.time() - start_t < 3.0:
            time.sleep(0.05)

        assert client.has_heart_rate_data() is True
        assert client.has_video_signal() is True

        hr_summary = client.get_heart_rate_summary()
        assert hr_summary["latest_hr"] == 80.0

        temp_summary = client.get_temperature_summary()
        assert temp_summary["latest_ambient_temp"] == 26.0
        assert temp_summary["latest_object_temp"] == 36.8

        device_status = client.get_device_status()
        assert device_status["transport"]["type"] == "wifi"
        assert device_status["is_connected"] is True

    finally:
        client.stop()
        server_tcp.close()

    print("✅ MCUClient 接入 WiFi 传输层多模态采集与录制测试全部通过！")


def test_dynamic_transport_switching():
    """测试系统在串口与 WiFi 模式间的动态无缝热切换。"""
    client = MCUClient()
    initial_info = client.get_transport_info()
    assert "type" in initial_info

    # 1. 切换至 WiFi 模式
    ok = client.switch_to_wifi(host="192.168.1.100", tcp_port=9999, udp_port=9998)
    assert ok is True
    wifi_info = client.get_transport_info()
    assert wifi_info["type"] == "wifi"
    assert wifi_info["host"] == "192.168.1.100"
    assert wifi_info["tcp_port"] == 9999
    assert wifi_info["udp_port"] == 9998

    # 2. 切换回 Serial 模式
    ok = client.switch_to_serial(port="COM7", baudrate=115200)
    assert ok is True
    serial_info = client.get_transport_info()
    assert serial_info["type"] == "serial"
    assert serial_info["port"] == "COM7"
    assert serial_info["baudrate"] == 115200

    print("✅ 串口与 WiFi 动态平滑切换测试全部通过！")


def test_hardware_router_mode_api():
    """测试 FastAPI hardware 路由中的 /hardware/mode 模式获取与切换接口。"""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routers.hardware import router as hardware_router

    app = FastAPI()
    app.include_router(hardware_router)
    client = TestClient(app)

    # 1. GET /hardware/mode 查询模式
    res = client.get("/hardware/mode")
    assert res.status_code == 200
    data = res.json()
    assert data["code"] == 0
    assert "transport" in data["data"]
    assert "is_connected" in data["data"]

    # 2. POST /hardware/mode 切换至 WiFi
    res = client.post(
        "/hardware/mode",
        json={"mode": "wifi", "host": "192.168.8.50", "tcp_port": 8888, "udp_port": 8889},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["code"] == 0
    assert data["data"]["type"] == "wifi"
    assert data["data"]["host"] == "192.168.8.50"

    # 3. POST /hardware/mode 切换至 Serial
    res = client.post(
        "/hardware/mode",
        json={"mode": "serial", "port": "COM4", "baudrate": 1000000},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["code"] == 0
    assert data["data"]["type"] == "serial"
    assert data["data"]["port"] == "COM4"

    # 4. 异常参数校验
    res = client.post("/hardware/mode", json={"mode": "bluetooth"})
    assert res.status_code == 400

    print("✅ Hardware Router API 接口测试全部通过！")


if __name__ == "__main__":
    test_wifi_transport_tcp_and_udp()
    test_mcu_client_with_wifi_transport()
    test_dynamic_transport_switching()
    test_hardware_router_mode_api()
    print("🎉 WiFi 传输模块全部单元测试通过！")
