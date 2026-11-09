"""硬件应用服务，隐藏采集统计与预览编码细节。"""
import asyncio

import cv2
import numpy as np
from core.hardware_profile import CAMERA_MAX_FPS


class HardwareService:
    def __init__(self, device):
        self.device = device

    def start(self, duration, capture_video=True):
        self.device.start_recording(duration=duration, capture_video=capture_video)

    def stop(self):
        self.device.stop_recording()
        return {
            "recorded_frames": len(self.device.get_recorded_frames(copy=False)),
            "audio_chunks": self.device.get_audio_summary()["chunks_count"],
            "hr_count": len(self.device.get_heart_rate_summary()["hr_list"]),
            "temp_count": len(self.device.get_temperature_summary()["ambient_temps"]),
        }

    def status(self):
        return self.device.get_device_status()

    def transport(self):
        return {
            "transport": self.device.get_transport_info(),
            "is_connected": self.device.is_connected,
            "is_recording": self.device.is_recording,
        }

    def switch(self, settings):
        if settings.mode == "wifi":
            self.device.switch_to_wifi(host=settings.host, tcp_port=settings.tcp_port, udp_port=settings.udp_port)
        else:
            self.device.switch_to_serial(port=settings.port, baudrate=settings.baudrate)
        return self.device.get_transport_info()

    async def frames(self, is_disconnected):
        try:
            while not self.device.is_stopped():
                if await is_disconnected():
                    break
                frame = self.device.get_latest_frame(copy=True)
                if frame is None:
                    frame = np.zeros((480, 640, 3), dtype=np.uint8)
                    connected = self.device.is_connected
                    label = "WAITING FOR CAMERA FRAME" if connected else "WAITING FOR MCU"
                    color = (0, 215, 255) if connected else (0, 0, 255)
                    cv2.putText(frame, label, (45, 240), cv2.FONT_HERSHEY_SIMPLEX, 0.8, color, 2)
                # 固件已做有损 JPEG 压缩；用高质量转码，减少预览再次压缩造成的细节损失。
                ok, buffer = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 95])
                if ok:
                    yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + buffer.tobytes() + b"\r\n"
                # 固件最高 10 fps，避免重复编码/发送同一图像到浏览器。
                await asyncio.sleep(1.0 / CAMERA_MAX_FPS)
        except (asyncio.CancelledError, GeneratorExit):
            return
