# -*-coding: utf-8 -*-
"""单片机（MCU/ESP32）通信协议编解码与数据解析层。

该模块为纯协议与数据层，不依赖任何具体的串口或硬件 I/O 实现。
支持数据帧定义、COBS 帧分帧与四类多模态数据包的解析：
- 0x01: 48000Hz 16-bit 单声道 PCM 音频
- 0x02: 8 字节双浮点环境温度与物体/体温 (<ff)
- 0x04: 4 字节单浮点 LD6002 雷达心率 (<f)
- 0xff: JPEG 图像流（包含旋转与镜像校正）
"""
from dataclasses import dataclass
from enum import IntEnum
import struct
from typing import Any, List, Optional, Tuple

from core.hardware_profile import AUDIO_PACKET_SIZE, FIXED_PACKET_SIZES, MAX_ENCODED_FRAME_SIZE

import cv2
import numpy as np

try:
    from cobs import cobs
except ImportError:
    cobs = None


class PacketType(IntEnum):
    """单片机硬件消息类型枚举。"""
    AUDIO = 0x01
    TEMPERATURE = 0x02
    HEART_RATE = 0x04
    CAMERA = 0xFF


@dataclass(frozen=True)
class TemperatureReading:
    """温度测量数据。"""
    ambient: float
    object_temp: float


@dataclass(frozen=True)
class HeartRateReading:
    """心率测量数据 (BPM)。"""
    bpm: float


@dataclass
class AudioChunk:
    """音频分片与计算后的音量、波形特征。"""
    raw_pcm: bytes
    volume: float
    wave: List[int]


@dataclass
class VideoFrame:
    """摄像头单帧图像（已校正方向）。"""
    image: np.ndarray


class MCUProtocolParser:
    """COBS 协议分帧与多模态数据包解析器。"""

    def __init__(self):
        self._buffer = bytearray()
        self._discard_until_delimiter = False

    def reset(self):
        """重置内部字节缓冲区。"""
        self._buffer.clear()
        self._discard_until_delimiter = False

    def feed_bytes(self, chunk: bytes) -> List[bytes]:
        """向缓冲区追加原始字节流，切分并返回经 COBS 解码的有效数据包列表。"""
        if not chunk or cobs is None:
            return []

        if self._discard_until_delimiter:
            delimiter = chunk.find(b"\x00")
            if delimiter < 0:
                return []
            chunk = chunk[delimiter + 1:]
            self._discard_until_delimiter = False
        self._buffer.extend(chunk)
        packets = self._buffer.split(b"\x00")
        self._buffer = packets.pop()
        if len(self._buffer) > MAX_ENCODED_FRAME_SIZE:
            self._buffer.clear()
            self._discard_until_delimiter = True

        decoded_list = []
        for packet in packets:
            if not packet or len(packet) > MAX_ENCODED_FRAME_SIZE:
                continue
            try:
                decoded = cobs.decode(packet)
                if decoded:
                    # mix.ino 的编码器省略尾零对应的最终空块。仅对已知定长
                    # 流帧补回恰好一个尾零；UDP 原始包不经过此兼容逻辑。
                    expected = FIXED_PACKET_SIZES.get(decoded[0])
                    if expected and len(decoded) == expected - 1:
                        decoded += b"\x00"
                    decoded_list.append(decoded)
            except cobs.DecodeError:
                continue

        return decoded_list

    @staticmethod
    def parse_packet(decoded_data: bytes) -> Tuple[Optional[PacketType], Any]:
        """解析单个已通过 COBS 解码的完整数据包，返回 (PacketType, 解析后强类型对象)。"""
        if not decoded_data:
            return None, None

        msg_type = decoded_data[0]

        # 1. 音频数据 (Type 0x01)
        if msg_type == PacketType.AUDIO and len(decoded_data) == AUDIO_PACKET_SIZE:
            raw_pcm = bytes(decoded_data[1:])
            volume = 0.0
            wave_samples: List[int] = []
            try:
                samples = np.frombuffer(raw_pcm, dtype="<i2")
                if len(samples) > 0:
                    rms = np.sqrt(np.mean(samples.astype(np.float32) ** 2))
                    volume = round(min(100.0, float(rms / 327.68)), 1)
                    step = max(1, len(samples) // 32)
                    # 保留有符号 PCM 振幅，供页面画真实波形，而非绝对值包络。
                    wave_samples = [round(int(s) / 32768 * 24, 1) for s in samples[::step][:32]]
            except Exception:
                pass
            return PacketType.AUDIO, AudioChunk(raw_pcm=raw_pcm, volume=volume, wave=wave_samples)

        # 2. 温度数据 (Type 0x02: <ff)
        elif msg_type == PacketType.TEMPERATURE and len(decoded_data) == 9:
            try:
                ambient, obj = struct.unpack("<ff", decoded_data[1:9])
                if -30.0 <= ambient <= 100.0 and -10.0 <= obj <= 120.0:
                    return PacketType.TEMPERATURE, TemperatureReading(
                        ambient=round(float(ambient), 2),
                        object_temp=round(float(obj), 2),
                    )
            except Exception:
                pass
            return None, None

        # 3. 心率数据 (Type 0x04: <f)
        elif msg_type == PacketType.HEART_RATE and len(decoded_data) == 5:
            try:
                hr = struct.unpack("<f", decoded_data[1:5])[0]
                if 30.0 <= hr <= 230.0:
                    return PacketType.HEART_RATE, HeartRateReading(bpm=round(float(hr), 2))
            except Exception:
                pass
            return None, None

        # 4. 图像数据 (Type 0xFF: JPEG)
        elif msg_type == PacketType.CAMERA and len(decoded_data) > 10 and decoded_data.startswith(b"\xff\xd8") and decoded_data.endswith(b"\xff\xd9"):
            try:
                np_arr = np.frombuffer(decoded_data, np.uint8)
                img = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)
                if img is not None:
                    # 硬件摄像头方向校正：逆时针旋转 90 度并水平镜像翻转
                    img = cv2.rotate(img, cv2.ROTATE_90_COUNTERCLOCKWISE)
                    img = cv2.flip(img, 1)
                    return PacketType.CAMERA, VideoFrame(image=img)
            except Exception:
                pass
            return None, None

        return None, None
