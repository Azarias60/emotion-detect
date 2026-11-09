import logging
import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

_DEFAULT_JWT_SECRET = "change-this-secret-in-production"
DATA_DIR = Path(os.getenv("EMOTION_DATA_DIR", BASE_DIR / "data"))
DATABASE_PATH = Path(os.getenv("EMOTION_DATABASE_PATH", DATA_DIR / "emotion_management.db"))
UPLOAD_DIR = Path(os.getenv("EMOTION_UPLOAD_DIR", DATA_DIR / "uploads"))
JWT_SECRET = os.getenv("EMOTION_JWT_SECRET", _DEFAULT_JWT_SECRET)
TOKEN_TTL_SECONDS = int(os.getenv("EMOTION_TOKEN_TTL_SECONDS", "43200"))

if JWT_SECRET == _DEFAULT_JWT_SECRET:
    logging.getLogger("emotion.management").warning(
        "EMOTION_JWT_SECRET 未设置，正在使用内置默认密钥；仅供本地开发，部署前必须配置"
    )

DATA_DIR.mkdir(parents=True, exist_ok=True)
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

