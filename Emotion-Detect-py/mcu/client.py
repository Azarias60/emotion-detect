# -*-coding: utf-8 -*-
"""单片机硬件设备客户端与多模态会话管理器。

负责串口连接生命周期、后台数据接收线程调度、数据帧解析分发、
会话录制控制及线程安全的多模态生理指标汇聚。
"""
import os
import threading
import time
from collections import deque
from typing import List, Optional

import numpy as np

from core.config import SERIAL_CONFIG, TRANSPORT_MODE, WIFI_CONFIG
from core.logger import get_logger
from core.hardware_profile import CAMERA_MAX_FPS
from mcu.audio_exporter import AudioPlayerWorker, save_pcm_chunks_to_wav
from mcu.control import parse_mode_confirmation
from mcu.protocol import (
    AudioChunk,
    HeartRateReading,
    MCUProtocolParser,
    PacketType,
    TemperatureReading,
    VideoFrame,
)
from mcu.transport import BaseTransport, SerialTransport, WiFiTransport

logger = get_logger(__name__)


def get_default_transport() -> BaseTransport:
    """根据系统全局环境变量配置创建默认通信传输实现 (Serial 或 WiFi)。"""
    if TRANSPORT_MODE == "wifi":
        return WiFiTransport(
            host=WIFI_CONFIG["host"],
            tcp_port=WIFI_CONFIG["tcp_port"],
            udp_port=WIFI_CONFIG["udp_port"],
        )
    return SerialTransport(
        port=SERIAL_CONFIG["port"],
        baudrate=SERIAL_CONFIG["baudrate"],
    )


class MCUClient:
    """单片机统一设备客户端。封装传输层通信、状态缓存与多模态数据会话。"""

    def __init__(self, transport: Optional[BaseTransport] = None):
        self._transport = transport or get_default_transport()
        self._transport_lock = threading.Lock()
        self._parser = MCUProtocolParser()
        self._audio_player = AudioPlayerWorker()

        # 并发控制锁与事件
        self._state_lock = threading.RLock()
        self._stop_event = threading.Event()
        self._listener_thread: Optional[threading.Thread] = None

        # 连接与录制状态
        self._is_connected = False
        self._requested_output_mode = None
        self._confirmed_output_mode = None
        self._mode_requested_at = 0.0
        self._is_recording = False
        self._capture_video = True
        self._record_duration = 60
        self._record_start_time = 0.0

        # 最新实时传感器值
        self._latest_hr: Optional[float] = None
        self._live_hr_list = deque(maxlen=128)
        self._latest_ambient_temp: Optional[float] = None
        self._latest_object_temp: Optional[float] = None
        self._latest_audio_volume: float = 0.0
        self._audio_wave = deque(maxlen=512)
        self._latest_frame: Optional[np.ndarray] = None

        # 当前会话录制数据缓冲
        self._hr_list: List[float] = []
        self._ambient_temps: List[float] = []
        self._object_temps: List[float] = []
        self._audio_chunks: List[bytes] = []
        self._recorded_audio_wave = deque(maxlen=512)
        self._recorded_frames: List[np.ndarray] = []
        self._frame_times: List[float] = []

    # --------------------------------------------------------------------------
    # 生命周期控制
    # --------------------------------------------------------------------------
    def start(self):
        """启动单片机后台常驻通信监听线程。"""
        if self._listener_thread and self._listener_thread.is_alive():
            return
        self._stop_event.clear()
        self._listener_thread = threading.Thread(
            target=self._worker_loop,
            daemon=True,
            name="MCU-Listener-Thread",
        )
        self._listener_thread.start()

        if os.getenv("ENABLE_MCU_AUDIO_PLAYBACK", "0") == "1":
            self._audio_player.start()

    def stop(self):
        """安全停止单片机后台监听与回放服务。"""
        self._stop_event.set()
        self.stop_recording()
        if self._listener_thread and self._listener_thread.is_alive():
            self._listener_thread.join(timeout=1.0)
        self._audio_player.stop()
        with self._transport_lock:
            self._transport.close()
        self._is_connected = False

    def is_stopped(self) -> bool:
        """检查硬件后台服务是否已处于终止状态。"""
        return self._stop_event.is_set()

    @property
    def is_connected(self) -> bool:
        """单片机硬件物理连接状态。"""
        return self._is_connected

    @property
    def is_recording(self) -> bool:
        """是否处于检测录制窗口中。"""
        with self._state_lock:
            if self._is_recording and time.monotonic() - self._record_start_time >= self._record_duration:
                self.stop_recording()
            return self._is_recording

    # --------------------------------------------------------------------------
    # 传输层模式动态切换 API
    # --------------------------------------------------------------------------
    def get_transport_info(self) -> dict:
        """获取当前使用的通信传输层配置与状态描述。"""
        with self._transport_lock:
            info = self._transport.get_info()
            status = "pending"
            if self._requested_output_mode and self._confirmed_output_mode == self._requested_output_mode:
                status = "confirmed"
            elif self._requested_output_mode and time.monotonic() - self._mode_requested_at > 3:
                status = "unconfirmed"
            info.update(output_mode=self._confirmed_output_mode, output_mode_status=status)
            return info

    def switch_transport(self, transport: BaseTransport) -> bool:
        """动态平滑切换底层通信传输层实现（无需重启系统）。"""
        with self._transport_lock:
            self.stop_recording()
            self._clear_live_data()
            old = self._transport
            try:
                old.close()
            except Exception:
                pass
            self._requested_output_mode = None
            self._confirmed_output_mode = None
            self._transport = transport
            self._parser.reset()
            self._is_connected = False
        logger.info(f"[硬件服务][SWITCH] 通信传输层已切换为: {transport.get_info().get('type')}")
        return True

    def switch_to_wifi(
        self,
        host: Optional[str] = None,
        tcp_port: Optional[int] = None,
        udp_port: Optional[int] = None,
    ) -> bool:
        """切换至 WiFi (TCP+UDP 双通道) 采集模式。"""
        target_host = host or WIFI_CONFIG["host"]
        target_tcp = tcp_port if tcp_port is not None else WIFI_CONFIG["tcp_port"]
        target_udp = udp_port if udp_port is not None else WIFI_CONFIG["udp_port"]
        new_transport = WiFiTransport(
            host=target_host,
            tcp_port=target_tcp,
            udp_port=target_udp,
        )
        return self.switch_transport(new_transport)

    def switch_to_serial(
        self,
        port: Optional[str] = None,
        baudrate: Optional[int] = None,
    ) -> bool:
        """切换至串口采集模式。"""
        target_port = port or SERIAL_CONFIG["port"]
        target_baud = baudrate if baudrate is not None else SERIAL_CONFIG["baudrate"]
        new_transport = SerialTransport(
            port=target_port,
            baudrate=target_baud,
        )
        return self.switch_transport(new_transport)

    # --------------------------------------------------------------------------
    # 会话录制控制
    # --------------------------------------------------------------------------
    def start_recording(self, duration: int = 60, capture_video: bool = True):
        """开启指定时长的全模态录制窗口并清空历史缓冲。"""
        with self._state_lock:
            self.stop_recording()
            with self._state_lock:
                self._hr_list.clear()
                self._ambient_temps.clear()
                self._object_temps.clear()
                self._audio_chunks.clear()
                self._recorded_audio_wave.clear()

            with self._state_lock:
                self._recorded_frames.clear()
                self._frame_times.clear()

            self._record_duration = duration
            self._capture_video = capture_video
            self._record_start_time = time.monotonic()
            self._is_recording = True

            logger.info(f"[硬件服务][START] 单片机全模态采集录制已启动，计划时长 {duration} 秒")

    def stop_recording(self):
        """停止录制窗口并锁定已缓存的音视频与生理数据。"""
        with self._state_lock:
            self._is_recording = False

    # --------------------------------------------------------------------------
    # 业务查询与指标提取 API (业务层面向高层抽象)
    # --------------------------------------------------------------------------
    def has_heart_rate_data(self) -> bool:
        """当前录制会话是否已捕获到有效心率数据。"""
        with self._state_lock:
            return len(self._hr_list) > 0

    def has_recorded_video(self) -> bool:
        """当前录制会话是否已捕获到摄像头画面帧。"""
        with self._state_lock:
            return len(self._recorded_frames) > 0

    def has_video_signal(self) -> bool:
        """当前是否有可用的实时视频画面输入。"""
        with self._state_lock:
            return self._latest_frame is not None

    def get_latest_frame(self, copy: bool = True) -> Optional[np.ndarray]:
        """安全读取当前最新摄像头画面帧。"""
        with self._state_lock:
            if self._latest_frame is None:
                return None
            return self._latest_frame.copy() if copy else self._latest_frame

    def get_recorded_frames(self, copy: bool = True) -> List[np.ndarray]:
        """安全读取当前录制窗口内的所有画面帧快照。"""
        with self._state_lock:
            if copy:
                return [f.copy() for f in self._recorded_frames]
            return list(self._recorded_frames)

    def get_heart_rate_summary(self) -> dict:
        """获取雷达 BPM；固件未提供逐搏 RR 间隔，HRV 不可用。"""
        with self._state_lock:
            values = list(self._hr_list)
            latest = self._latest_hr
            live_values = list(self._live_hr_list)

        avg = round(float(np.mean(values)), 2) if values else 0.0
        return {
            "hr_list": [round(v, 2) for v in values],
            "live_hr_list": live_values,
            "avg_hr": avg,
            "latest_hr": latest,
            "hrv_indices": {key: None for key in ("SDNN", "RMSSD", "NN50", "pNN50")},
            "hrv_status": "unavailable_no_rr_intervals",
            "source": "ld6002_radar",
        }

    def get_temperature_summary(self) -> dict:
        """获取当前环境与物体温度的标准化业务数据包。"""
        with self._state_lock:
            amb_list = [round(t, 2) for t in self._ambient_temps]
            obj_list = [round(t, 2) for t in self._object_temps]
            latest_amb = self._latest_ambient_temp
            latest_obj = self._latest_object_temp

        avg_amb = round(float(np.mean(amb_list)), 2) if amb_list else None
        avg_obj = round(float(np.mean(obj_list)), 2) if obj_list else None
        return {
            "ambient_temps": amb_list,
            "object_temps": obj_list,
            "avg_ambient_temp": avg_amb,
            "avg_object_temp": avg_obj,
            "latest_ambient_temp": latest_amb,
            "latest_object_temp": latest_obj,
            "status": "connected" if self._is_connected else "disconnected",
        }

    def get_audio_summary(self) -> dict:
        """获取音频实时音量、波形与分块统计。"""
        with self._state_lock:
            return {
                "latest_volume": self._latest_audio_volume,
                "wave": list(self._audio_wave),
                "recorded_wave": list(self._recorded_audio_wave),
                "chunks_count": len(self._audio_chunks),
            }

    def get_video_summary(self) -> dict:
        """获取视频采集状态概览。"""
        with self._state_lock:
            return {
                "recorded_frames_count": len(self._recorded_frames),
                "has_signal": self._latest_frame is not None,
            }

    def get_device_status(self) -> dict:
        """汇总单片机设备当前全部传感器、音视频与连接状态（用于前端看板与探活）。"""
        return {
            "is_connected": self._is_connected,
            "is_recording": self.is_recording,
            "transport": self.get_transport_info(),
            "heart_rate": self.get_heart_rate_summary(),
            "temperature": self.get_temperature_summary(),
            "audio": self.get_audio_summary(),
            "video": self.get_video_summary(),
        }

    def save_recorded_audio(self, temp_dir: str, base_filename: str = "mcu_audio") -> dict:
        """将当前录制会话中的单片机 PCM 音频导出为标准与 16kHz 目标 WAV 文件。"""
        with self._state_lock:
            chunks = list(self._audio_chunks)
        return save_pcm_chunks_to_wav(chunks, temp_dir, base_filename)

    # --------------------------------------------------------------------------
    # 内部数据处理与监听主循环
    # --------------------------------------------------------------------------
    def _clear_live_data(self):
        """连接失效时清除实时值，保留已录制会话供分析。"""
        with self._state_lock:
            self._latest_hr = None
            self._live_hr_list.clear()
            self._latest_ambient_temp = None
            self._latest_object_temp = None
            self._latest_audio_volume = 0.0
            self._audio_wave.clear()
        with self._state_lock:
            self._latest_frame = None

    def _receive_once(self):
        """在传输锁内执行，避免模式切换混入旧连接的数据或半帧。"""
        transport = self._transport
        if not transport.is_open():
            self._parser.reset()
            self._clear_live_data()
            self._confirmed_output_mode = None
            self._requested_output_mode = None
            self._is_connected = transport.open()
            if not self._is_connected:
                return False
            self._requested_output_mode = transport.request_output_mode()
            self._mode_requested_at = time.monotonic()
            logger.info("单片机通信建立成功，已请求输出模式: %s", transport.get_info())
        packets = transport.read_packets()
        chunk = transport.read(4096)
        self._is_connected = transport.is_open()
        if not self._is_connected:
            self._parser.reset()
            self._clear_live_data()
            return False
        for packet in packets:
            self._handle_decoded_packet(packet)
        for packet in self._parser.feed_bytes(chunk):
            self._handle_decoded_packet(packet)
        return True

    def _worker_loop(self):
        """连接重试与收包调度；录制截止时间不依赖是否连接成功。"""
        while not self._stop_event.is_set():
            self.is_recording  # 检查本地录制窗口截止时间
            with self._transport_lock:
                if self._stop_event.is_set():
                    break
                try:
                    connected = self._receive_once()
                except Exception:
                    logger.exception("硬件收包失败")
                    self._is_connected = False
                    self._transport.close()
                    self._parser.reset()
                    self._clear_live_data()
                    connected = False
            self._stop_event.wait(0.001 if connected else 0.2)

    def get_recorded_fps(self) -> float:
        """用接收时间恢复会话平均帧率；单帧回退到固件上限。"""
        with self._state_lock:
            if len(self._frame_times) < 2:
                return CAMERA_MAX_FPS
            elapsed = self._frame_times[-1] - self._frame_times[0]
            return min(CAMERA_MAX_FPS, (len(self._frame_times) - 1) / elapsed) if elapsed > 0 else CAMERA_MAX_FPS

    def _handle_decoded_packet(self, decoded_data: bytes):
        """将解码后的单包数据映射为多模态对象并写入状态缓存。"""
        confirmation = parse_mode_confirmation(decoded_data)
        if confirmation is not None:
            self._confirmed_output_mode = confirmation.mode
            return
        pkg_type, parsed = self._parser.parse_packet(decoded_data)
        if pkg_type is None or parsed is None:
            return

        with self._state_lock:
            if pkg_type == PacketType.AUDIO and isinstance(parsed, AudioChunk):
                self._latest_audio_volume = parsed.volume
                self._audio_wave.extend(parsed.wave)
                if self.is_recording:
                    with self._state_lock:
                        self._audio_chunks.append(parsed.raw_pcm)
                        self._recorded_audio_wave.extend(parsed.wave)
                if os.getenv("ENABLE_MCU_AUDIO_PLAYBACK", "0") == "1":
                    self._audio_player.put_chunk(parsed.raw_pcm)

            elif pkg_type == PacketType.TEMPERATURE and isinstance(parsed, TemperatureReading):
                self._latest_ambient_temp = parsed.ambient
                self._latest_object_temp = parsed.object_temp
                if self.is_recording:
                    with self._state_lock:
                        self._ambient_temps.append(parsed.ambient)
                        self._object_temps.append(parsed.object_temp)

            elif pkg_type == PacketType.HEART_RATE and isinstance(parsed, HeartRateReading):
                self._latest_hr = parsed.bpm
                self._live_hr_list.append(parsed.bpm)
                if self.is_recording:
                    with self._state_lock:
                        self._hr_list.append(parsed.bpm)

            elif pkg_type == PacketType.CAMERA and isinstance(parsed, VideoFrame):
                with self._state_lock:
                    self._latest_frame = parsed.image
                    if self.is_recording and self._capture_video:
                        self._recorded_frames.append(parsed.image.copy())
                        self._frame_times.append(time.monotonic())
