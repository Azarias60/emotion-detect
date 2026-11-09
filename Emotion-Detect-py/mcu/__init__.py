# -*-coding: utf-8 -*-
"""MCU 单片机与外设通信封装包。

提供协议解析 (protocol)、底层传输 (transport)、音频导出 (audio_exporter) 以及统一设备会话管理 (client)。
"""
from mcu.audio_exporter import save_pcm_chunks_to_wav
from mcu.client import MCUClient, get_default_transport
from mcu.protocol import (
    AudioChunk,
    HeartRateReading,
    MCUProtocolParser,
    PacketType,
    TemperatureReading,
    VideoFrame,
)
from mcu.transport import BaseTransport, SerialTransport, WiFiTransport

# 默认全局单例
mcu_client = MCUClient()

__all__ = [
    "mcu_client",
    "MCUClient",
    "save_pcm_chunks_to_wav",
    "get_default_transport",
    "MCUProtocolParser",
    "PacketType",
    "TemperatureReading",
    "HeartRateReading",
    "AudioChunk",
    "VideoFrame",
    "BaseTransport",
    "SerialTransport",
    "WiFiTransport",
]
