# -*-coding: utf-8 -*-
"""唯一配置源：路径、环境变量、外部服务与权重位置。

仓库根目录下的 .env 会被加载（不覆盖已存在的环境变量；无文件时静默跳过）。
设备接入契约（信令 URL、通道协议）由 esp32s3capture 固件决定，服务端仅
被动应答，无需在此配置硬件参数。
"""
import logging
import os
from datetime import timedelta, timezone

from dotenv import load_dotenv

SERVER_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(SERVER_DIR)
load_dotenv(os.path.join(ROOT_DIR, ".env"))

DATA_DIR = os.getenv("EMOTION_DATA_DIR", os.path.join(SERVER_DIR, "data"))
WEIGHTS_DIR = os.path.join(SERVER_DIR, "weights")

DATABASE_PATH = os.getenv("EMOTION_DATABASE_PATH", os.path.join(DATA_DIR, "emotion_management.db"))
UPLOAD_DIR = os.getenv("EMOTION_UPLOAD_DIR", os.path.join(DATA_DIR, "uploads"))

# --- 管理域 ---
_DEFAULT_JWT_SECRET = "change-this-secret-in-production"
JWT_SECRET = os.getenv("EMOTION_JWT_SECRET", _DEFAULT_JWT_SECRET)
TOKEN_TTL_SECONDS = int(os.getenv("EMOTION_TOKEN_TTL_SECONDS", "43200"))
# 报表日期筛选的本地时区：报告时间戳以 UTC 存储，用户选择的日期边界按此时区换算成 UTC 再比较。
REPORT_TZ = timezone(timedelta(hours=int(os.getenv("EMOTION_REPORT_TZ_OFFSET_HOURS", "8"))))

if JWT_SECRET == _DEFAULT_JWT_SECRET:
    logging.getLogger("physiosense.settings").warning(
        "EMOTION_JWT_SECRET 未设置，正在使用内置默认密钥；仅供本地开发，部署前必须配置"
    )

# AI 配置（本地 Strata 语言模型引擎）
AI_CONFIG = {
    "base_url": os.getenv("EMOTION_LLM_BASE_URL", "http://127.0.0.1:8082/v1"),
    "api_key": os.getenv("EMOTION_LLM_API_KEY", "strata-local"),
    "model": os.getenv("EMOTION_LLM_MODEL", "qwen3.8-flash-next-iq3_s"),
    # 本地模型默认开启思考模式，正文 token 会被思考耗尽导致空回复；none 显式关闭
    "reasoning_effort": os.getenv("EMOTION_LLM_REASONING_EFFORT", "none"),
    # 文本生成任务使用同一套生成参数；不在各 reader 中设置个别默认值。
    "temperature": float(os.getenv("EMOTION_LLM_TEMPERATURE", "0.5")),
    "max_tokens": int(os.getenv("EMOTION_LLM_MAX_TOKENS", "1800")),
    "timeout": float(os.getenv("EMOTION_LLM_TIMEOUT", "120")),
}

# --- 运行时进程配置（分析线程池 / 语音设备） ---
ANALYSIS_WORKERS = max(1, int(os.getenv("EMOTION_ANALYSIS_WORKERS", "4")))
ASR_DEVICE = os.getenv("EMOTION_ASR_DEVICE", "auto").strip()
# 全模态统一采集滑动窗口时长（秒）：音频缓冲、视频帧日志等按真实时间淘汰，
# 各模态一律引自此处，不再各自维护帧数/条数上限。
CAPTURE_WINDOW_SECONDS = float(os.getenv("EMOTION_CAPTURE_WINDOW_SECONDS", "60"))
# --- 分布式部署（P5）：信令授权与跨源 ---
# 设备入流令牌（WHIP Bearer）：由部署配置注入并可轮换；未配置时设备
# 会话一律拒绝（缺省关闭，不依赖"知道 URL"）。
DEVICE_SIGNAL_TOKEN = os.getenv("EMOTION_DEVICE_SIGNAL_TOKEN", "")
# 生产 CORS 来源（逗号分隔）；默认 * 保持本地调试与通配契约。
CORS_ALLOW_ORIGINS = [
    origin.strip() for origin in os.getenv("EMOTION_CORS_ORIGINS", "*").split(",") if origin.strip()
]
# --- 权重位置（weights/ 集中存放，路径经 git mv 固化） ---
FACE_DETECTOR_WEIGHTS = os.path.join(WEIGHTS_DIR, "face", "detector", "rfb-face-mask.pth")
FACE_EMOTION_WEIGHTS = os.path.join(WEIGHTS_DIR, "face", "emotion", "latest_model_099_94.7200.pth")
TONGUE_WEIGHTS_DIR = WEIGHTS_DIR  # 各子模型在 weights/tongue/<name>/weights/model-29.pth
VOICE_MODEL_DIR = os.path.join(WEIGHTS_DIR, "voice", "SenseVoiceSmall")
VOICE_VAD_DIR = os.path.join(WEIGHTS_DIR, "voice", "speech_fsmn_vad_zh-cn-16k-common-pytorch")
FATIGUE_LANDMARKS_PATH = os.path.join(WEIGHTS_DIR, "fatigue", "shape_predictor_68_face_landmarks.dat")
