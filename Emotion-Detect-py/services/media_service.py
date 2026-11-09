"""音视频文件读写与格式转换；采集设备由调用方传入。"""
import os

import cv2
from core.hardware_profile import CAMERA_MAX_FPS


def write_recorded_video(temp_dir: str, filename: str = "recorded.mp4", *, device) -> str | None:
    """将单片机采集的图像序列拼装保存为 mp4 视频。"""
    frames = device.get_recorded_frames(copy=True)
    if not frames:
        return None
    video_path = os.path.join(temp_dir, filename)
    height, width = frames[0].shape[:2]
    fps = device.get_recorded_fps() if hasattr(device, "get_recorded_fps") else CAMERA_MAX_FPS
    writer = cv2.VideoWriter(video_path, cv2.VideoWriter_fourcc(*"mp4v"), fps, (width, height))
    try:
        if not writer.isOpened():
            raise RuntimeError("无法创建硬件录像文件")
        for frame in frames:
            writer.write(frame)
    finally:
        writer.release()
    return video_path


def write_recorded_audio(temp_dir: str, base_filename: str = "mcu_audio", *, device) -> str | None:
    """将单片机采集的 PCM 音频提取为 16kHz WAV 音频文件。"""
    res = device.save_recorded_audio(temp_dir, base_filename)
    return res.get("asr_wav")


def resolve_session_audio(temp_dir: str, *, device) -> str | None:
    """只导出后端设备录音；没有有效录音时返回缺失。"""
    audio_path = write_recorded_audio(temp_dir, "session_mcu_audio", device=device)
    if audio_path and os.path.exists(audio_path) and os.path.getsize(audio_path) > 1024:
        return audio_path
    return None
