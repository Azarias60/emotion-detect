# -*-coding: utf-8 -*-
"""P0 骨架门禁：配置默认值与应用装配。"""
import os

from fastapi.testclient import TestClient

from server import settings
from server.app import create_app

def test_settings_layout():
    assert settings.WEIGHTS_DIR.endswith(os.path.join("server", "weights"))
    assert settings.VOICE_MODEL_DIR.endswith(os.path.join("voice", "SenseVoiceSmall"))
    assert settings.DATA_DIR.endswith(os.path.join("server", "data"))


def test_healthz():
    # 不进入 lifespan（模型装配在契约测试外单独验证）；healthz 与装配无关
    client = TestClient(create_app())
    res = client.get("/healthz")
    assert res.status_code == 200
    body = res.json()
    assert body["code"] == 0
    assert body["data"]["status"] == "ok"
