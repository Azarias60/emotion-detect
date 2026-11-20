# -*- coding: utf-8 -*-
"""共享测试装配：采集内核与各域服务（无串口、无网络、无权重）。

内核种子函数把帧/传感器/音频证据直接写入内存端口，等价于一次设备
state 快照与一帧解码产物；``attach_fake_device`` 用 duck-typed 会话让
``is_connected()`` 为真。模型槽位归各域服务，替身直接替换对应槽位。
"""
import time
from types import SimpleNamespace

import cv2
import numpy as np

from server.capture.service import create_kernel
from server.bootstrap import assemble

assembly = assemble(kernel=create_kernel())
kernel = assembly.kernel
expression = assembly.expression
speech = assembly.speech
tongue = assembly.tongue


def attach_fake_device():
    """挂一个在线会话替身，使内核 ``is_connected()`` 为真。"""
    kernel.hub.current = SimpleNamespace(
        closed=False, peer=SimpleNamespace(connectionState="connected"))
    return kernel.hub.current


def detach_fake_device():
    kernel.hub.current = None


def seed_frame(frame=None, size=(320, 240), brightness=160):
    """发布一帧测试画面（原始 JPEG 字节直接入库，解码按需进行）。"""
    if frame is None:
        height, width = size
        frame = np.full((height, width, 3), brightness, dtype=np.uint8)
        frame[10:40, 10:60] = 40
    # 软件夹具的返回值代表规范方向；设备日志中的原始 JPEG 仍需经过
    # 采集边界的逆时针 90°旋转和水平镜像后才能得到该矩阵。
    raw_frame = np.rot90(np.fliplr(frame), k=-1).copy()
    ok, buf = cv2.imencode(".jpg", raw_frame)
    assert ok
    kernel.publish_frame(buf.tobytes(), time.monotonic(), time.monotonic())
    return frame


def seed_sensors(bpm=None, ambient=24.5, object_temp=36.2):
    """写入一次有效 ``state`` 快照证据（心率可选）。"""
    kernel.update_sensors({
        "v": 1, "type": "state", "enabled": True, "run": 1, "audio_base_ms": 0,
        "temperature": {
            "valid": ambient is not None, "sampled_at_us": 1000,
            "ambient_c": ambient, "object_c": object_temp, "error": None,
        },
        "heart_rate": {
            "valid": bpm is not None, "sampled_at_us": 1000,
            "bpm": bpm, "error": None,
        },
    })


def seed_audio(pcm: bytes) -> None:
    kernel.audio_buffer.extend(pcm)


def reset_capture():
    kernel.clear_media_state()
    kernel.hub.current = None
