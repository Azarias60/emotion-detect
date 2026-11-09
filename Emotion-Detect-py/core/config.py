# -*-coding: utf-8 -*-
"""系统全局常量、路径与环境配置。"""
import os
import sys

from dotenv import load_dotenv
from core.hardware_profile import (SERIAL_BAUDRATE, TCP_PORT, UDP_AUDIO_PORT,
    AUDIO_RATE, AUDIO_CHANNELS, AUDIO_SAMPLE_WIDTH, AUDIO_SAMPLES_PER_PACKET)

# --- 1. 核心路径定义 ---
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 加载后端目录下的 .env（不覆盖已存在的环境变量；无 .env 文件时静默跳过）
load_dotenv(os.path.join(BASE_DIR, ".env"))
FACE_DET_PATH = os.path.join(BASE_DIR, "face_detection")

# 将项目根加入 sys.path，保证任意 cwd 下包导入正常
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

# --- 2. 模型权重路径 ---
LANDMARK_MODEL_PATH = os.path.join(BASE_DIR, "detection_models", "assets", "shape_predictor_68_face_landmarks.dat")
BP_MODEL_PATH = os.getenv("BP_MODEL_PATH", os.path.join(BASE_DIR, "detection_models", "resnet_ppg_nonmixed.h5"))

# --- 3. 外部服务与硬件配置 ---
# 默认指向本地 llama-server 服务 (Ternary-Bonsai-2-27B)；配置 SILICONFLOW_* 可切换云端模型
AI_CONFIG = {
    "base_url": os.getenv("LOCAL_LLM_BASE_URL", os.getenv("SILICONFLOW_BASE_URL", "http://127.0.0.1:1919/v1")),
    "api_key": os.getenv("LOCAL_LLM_API_KEY", os.getenv("SILICONFLOW_API_KEY", "local")),
    "model": os.getenv("LOCAL_LLM_MODEL", os.getenv("SILICONFLOW_MODEL", "Ternary-Bonsai-2-27B-PQ2_0")),
}

TRANSPORT_MODE = os.getenv("EMOTION_TRANSPORT_MODE", "serial").lower()

SERIAL_CONFIG = {
    "port": os.getenv("EMOTION_SERIAL_PORT", "COM3"),
    "baudrate": int(os.getenv("EMOTION_SERIAL_BAUDRATE", str(SERIAL_BAUDRATE))),
}

WIFI_CONFIG = {
    "host": os.getenv("EMOTION_WIFI_HOST", "192.168.8.11"),
    "tcp_port": int(os.getenv("EMOTION_WIFI_TCP_PORT", str(TCP_PORT))),
    "udp_port": int(os.getenv("EMOTION_WIFI_UDP_PORT", str(UDP_AUDIO_PORT))),
}

AUDIO_CONFIG = {
    "rate": AUDIO_RATE,
    "channels": AUDIO_CHANNELS,
    "sample_width": AUDIO_SAMPLE_WIDTH,  # 16-bit (2 bytes)
    "frames_per_buffer": AUDIO_SAMPLES_PER_PACKET,
    "target_rate": 16000,  # FunASR SenseVoice 采样率
}
