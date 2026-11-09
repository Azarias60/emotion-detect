# -*- coding: utf-8 -*-
"""数字人 TTS 服务（edge-tts / 微软在线语音，免费无需 key）。

默认音色 zh-CN-YunxiaNeural（云霞）：Male + Cartoon + Cute——可爱卡通小男孩。
返回 MP3 音频（base64）+ 词边界时间戳（毫秒）——前端用词边界驱动数字人口型，
比音量包络精确（edge-tts 直接给出每个词的起止时间）。

用法：
    python tts_service.py            # 起服务，默认 8199 端口
    curl -X POST http://127.0.0.1:8199/tts -H "Content-Type: application/json" \\
         -d "{\\"text\\":\\"你好呀\\"}"
响应：
    {"audio_base64": "...", "duration_ms": 3200,
     "boundaries": [{"t0": 0, "t1": 480, "text": "你好"}, ...]}
"""

import asyncio
import base64
import json

import edge_tts
import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

app = FastAPI(title="数字人 TTS 服务")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# 可爱卡通小男孩（微软云霞）：Cartoon + Cute + Male
DEFAULT_VOICE = "zh-CN-YunxiaNeural"
# 备选：zh-CN-YunxiNeural（阳光少年）、zh-CN-YunxiaNeural 提音调更稚嫩
VOICE_PRESETS = {
    "cute_boy": {"voice": "zh-CN-YunxiaNeural", "rate": "+12%", "pitch": "+6Hz"},
    "sunny_boy": {"voice": "zh-CN-YunxiNeural", "rate": "+8%", "pitch": "+2Hz"},
}


class TtsRequest(BaseModel):
    text: str
    preset: str = "cute_boy"  # cute_boy / sunny_boy
    voice: str | None = None  # 直接指定音色（覆盖 preset）
    rate: str | None = None
    pitch: str | None = None


@app.get("/health")
async def health():
    return {"ok": True, "default_preset": "cute_boy", "presets": list(VOICE_PRESETS)}


@app.post("/tts")
async def tts(req: TtsRequest):
    cfg = dict(VOICE_PRESETS.get(req.preset, VOICE_PRESETS["cute_boy"]))
    if req.voice:
        cfg["voice"] = req.voice
    if req.rate:
        cfg["rate"] = req.rate
    if req.pitch:
        cfg["pitch"] = req.pitch

    communicate = edge_tts.Communicate(
        req.text,
        cfg["voice"],
        rate=cfg.get("rate", "+0%"),
        pitch=cfg.get("pitch", "+0Hz"),
        boundary="WordBoundary",  # 词级边界：口型按词驱动
    )
    audio = bytearray()
    boundaries = []
    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            audio += chunk["data"]
        elif chunk["type"] == "WordBoundary":
            # offset/duration 单位是 100ns，转毫秒
            boundaries.append({
                "t0": round(chunk["offset"] / 10000),
                "t1": round((chunk["offset"] + chunk["duration"]) / 10000),
                "text": chunk.get("text", ""),
            })
    duration = boundaries[-1]["t1"] if boundaries else 0
    return {
        "audio_base64": base64.b64encode(bytes(audio)).decode(),
        "duration_ms": duration,
        "boundaries": boundaries,
        "voice": cfg["voice"],
    }


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8199)
