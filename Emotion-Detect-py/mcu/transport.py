# -*-coding: utf-8 -*-
"""单片机硬件通信传输抽象、串口与 WiFi 网络双通道实现。"""
from abc import ABC, abstractmethod
from mcu.control import MODE_COMMANDS
from core.hardware_profile import AUDIO_PACKET_SIZE, TCP_PORT, UDP_AUDIO_PORT
import queue
import socket
import threading
from typing import Any, Dict, List, Optional

try:
    import serial
except ImportError:
    serial = None


class BaseTransport(ABC):
    """通信传输抽象基类。"""

    @abstractmethod
    def open(self) -> bool:
        """建立连接。"""
        pass

    @abstractmethod
    def close(self):
        """关闭连接。"""
        pass

    @abstractmethod
    def is_open(self) -> bool:
        """检查连接是否处于打开状态。"""
        pass

    @abstractmethod
    def read(self, max_bytes: int = 4096) -> bytes:
        """非阻塞读取可用字节流。"""
        pass

    @abstractmethod
    def reset_buffers(self):
        """清空输入输出缓冲区。"""
        pass

    def read_packets(self) -> list:
        """读取直接解包或独立数据包（如 WiFi 模式下的 UDP 数据包），默认返回空列表。"""
        return []

    def request_output_mode(self):
        """没有控制协议的测试/扩展传输可保持空实现。"""
        return None

    def get_info(self) -> dict:
        """获取当前传输层配置与状态描述。"""
        return {"type": self.__class__.__name__}


class SerialTransport(BaseTransport):
    """基于 PySerial 的物理串口通信实现。"""

    def __init__(self, port: str, baudrate: int, timeout: float = 0.1):
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout
        self._ser: Optional[serial.Serial] = None

    def open(self) -> bool:
        if serial is None:
            return False
        try:
            self.close()
            self._ser = serial.Serial(self.port, self.baudrate, timeout=self.timeout, write_timeout=1.0)
            self._ser.reset_input_buffer()
            return True
        except Exception:
            self._ser = None
            return False

    def close(self):
        if self._ser is not None:
            try:
                self._ser.close()
            except Exception:
                pass
            self._ser = None

    def is_open(self) -> bool:
        return self._ser is not None and getattr(self._ser, "is_open", False)

    def read(self, max_bytes: int = 4096) -> bytes:
        if not self.is_open() or self._ser is None:
            return b""
        try:
            waiting = self._ser.in_waiting
            if waiting > 0:
                count = min(waiting, max_bytes)
                return self._ser.read(count)
        except Exception:
            self.close()
        return b""

    def reset_buffers(self):
        if self.is_open() and self._ser is not None:
            try:
                self._ser.reset_input_buffer()
            except Exception:
                pass

    def request_output_mode(self):
        command = MODE_COMMANDS["serial"]
        if self._ser is None or self._ser.write(command) != len(command):
            raise OSError("USB 模式命令发送失败")
        return "serial"

    def get_info(self) -> dict:
        return {
            "type": "serial",
            "port": self.port,
            "baudrate": self.baudrate,
            "is_open": self.is_open(),
        }


class WiFiTransport(BaseTransport):
    """基于 TCP (传感器+图像流) 与 UDP (音频流) 的 ESP32 无线网络传输实现。"""

    def __init__(
        self,
        host: str = "192.168.8.11",
        tcp_port: int = TCP_PORT,
        udp_port: int = UDP_AUDIO_PORT,
        timeout: float = 0.1,
        connect_timeout: float = 5.0,
    ):
        self.host = host
        self.tcp_port = tcp_port
        self.udp_port = udp_port
        self.timeout = timeout
        self.connect_timeout = connect_timeout
        self._peer_ip = None
        self._local_ip = None
        self._last_error = None
        self._tcp_bytes_received = 0
        self._udp_packets_received = 0

        self._tcp_sock: Optional[socket.socket] = None
        self._udp_sock: Optional[socket.socket] = None
        self._udp_queue: queue.Queue = queue.Queue(maxsize=500)
        self._udp_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()

    def open(self) -> bool:
        self.close()
        self._stop_event.clear()
        self._local_ip = None
        self._tcp_bytes_received = 0
        self._udp_packets_received = 0

        # 固件在 TCP 建连后立即向该客户端 IP 的 8889 端口发音频。
        # 先绑定 UDP，且将 socket 立即纳入 close() 管理，失败时不泄漏。
        try:
            self._udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self._udp_sock.settimeout(self.timeout)
            self._udp_sock.bind(("0.0.0.0", self.udp_port))
            self._tcp_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._tcp_sock.settimeout(self.connect_timeout)
            self._tcp_sock.connect((self.host, self.tcp_port))
            self._peer_ip = self._tcp_sock.getpeername()[0]
            self._local_ip = self._tcp_sock.getsockname()[0]
            self._last_error = None
            self._tcp_sock.settimeout(self.timeout)
        except Exception as error:
            self._last_error = str(error)
            self.close()
            return False

        # 3. 启动后台 UDP 音频接收线程
        self._udp_thread = threading.Thread(
            target=self._udp_listener_loop,
            daemon=True,
            name="WiFiTransport-UDP-Listener",
        )
        self._udp_thread.start()
        return True

    def _udp_listener_loop(self):
        """后台线程接收 UDP 音频包 (保持低延迟，避免缓冲堆积与丢包)。"""
        while not self._stop_event.is_set():
            sock = self._udp_sock
            if sock is None:
                break
            try:
                data, sender = sock.recvfrom(2048)
                if sender[0] == self._peer_ip and len(data) == AUDIO_PACKET_SIZE and data[0] == 0x01:
                    self._udp_packets_received += 1
                    # 队列已满时丢弃最早的分片
                    if self._udp_queue.full():
                        try:
                            self._udp_queue.get_nowait()
                        except queue.Empty:
                            pass
                    self._udp_queue.put_nowait(data)
            except socket.timeout:
                continue
            except Exception:
                if self._stop_event.is_set():
                    break

    def close(self):
        self._stop_event.set()
        with self._lock:
            if self._tcp_sock is not None:
                try:
                    self._tcp_sock.shutdown(socket.SHUT_RDWR)
                except Exception:
                    pass
                try:
                    self._tcp_sock.close()
                except Exception:
                    pass
                self._tcp_sock = None

            if self._udp_sock is not None:
                try:
                    self._udp_sock.close()
                except Exception:
                    pass
                self._udp_sock = None

        if self._udp_thread and self._udp_thread.is_alive():
            if threading.current_thread() != self._udp_thread:
                self._udp_thread.join(timeout=0.5)
            self._udp_thread = None

        self.reset_buffers()

    def is_open(self) -> bool:
        return self._tcp_sock is not None

    def read(self, max_bytes: int = 4096) -> bytes:
        if not self.is_open() or self._tcp_sock is None:
            return b""
        try:
            chunk = self._tcp_sock.recv(max_bytes)
            if not chunk:
                self._last_error = "设备关闭了 TCP 连接"
                # 对端断开连接
                self.close()
                return b""
            self._tcp_bytes_received += len(chunk)
            return chunk
        except socket.timeout:
            return b""
        except Exception as error:
            self._last_error = str(error)
            self.close()
            return b""

    def read_packets(self) -> list:
        """非阻塞批量获取当前队列中缓存的已到达 UDP 音频包。"""
        packets = []
        while not self._udp_queue.empty():
            try:
                packets.append(self._udp_queue.get_nowait())
            except queue.Empty:
                break
        return packets

    def reset_buffers(self):
        while not self._udp_queue.empty():
            try:
                self._udp_queue.get_nowait()
            except queue.Empty:
                break

    def request_output_mode(self):
        if self._tcp_sock is None:
            raise OSError("WiFi 控制连接未建立")
        self._tcp_sock.sendall(MODE_COMMANDS["wifi"])
        return "wifi"

    def get_info(self) -> dict:
        return {
            "type": "wifi",
            "host": self.host,
            "tcp_port": self.tcp_port,
            "udp_port": self.udp_port,
            "local_ip": self._local_ip,
            "last_error": self._last_error,
            "tcp_bytes_received": self._tcp_bytes_received,
            "udp_packets_received": self._udp_packets_received,
            "is_open": self.is_open(),
        }
