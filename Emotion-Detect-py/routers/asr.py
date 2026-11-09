# -*-coding: utf-8 -*-
"""轻量 ASR 转写端点：复用 lifespan 已常驻的 SenseVoiceSmall。

数字人引擎（digital-human/livetalking）的对话 ASR 走这里——比 whisper small
中文准（人名/简体）且快 3~15 倍；模型随 8081 生命周期常驻，引擎零新依赖。
"""
import io
import tempfile
from pathlib import Path

import numpy as np
import soundfile as sf
from fastapi import APIRouter, HTTPException, UploadFile, File

from core.logger import get_logger
from core.state import models

logger = get_logger(__name__)

router = APIRouter(tags=["语音转写"])


@router.post("/asr/transcribe")
async def transcribe(file: UploadFile = File(...)):
    """PCM/WAV 音频 → 中文文本。供数字人引擎的实时对话 ASR 调用。"""
    voice = getattr(models, "voice_model", None)
    if voice is None:
        raise HTTPException(503, "语音模型未加载（8081 启动失败或仍在加载）")
    data = await file.read()
    if not data:
        raise HTTPException(400, "空音频")
    tmp = None
    try:
        # 引擎发来的是 16kHz 单声道 WAV；soundfile 能直读则直读
        try:
            audio, sr = sf.read(io.BytesIO(data), dtype="float32")
        except Exception:
            tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
            tmp.write(data)
            tmp.close()
            audio, sr = sf.read(tmp.name, dtype="float32")
        if audio.ndim > 1:
            audio = audio.mean(axis=1)
        if sr != 16000:
            # numpy 线性插值重采样（语音 16k 足够，避免引入 resampy 依赖）
            dur = int(len(audio) * 16000 / sr)
            x_old = np.linspace(0, 1, len(audio), endpoint=False)
            x_new = np.linspace(0, 1, dur, endpoint=False)
            audio = np.interp(x_new, x_old, audio)
        result = voice.generate(
            input=audio,
            cache={},
            language="zh",
            use_itn=True,
            batch_size_s=60,
        )
        text = result[0].get("text", "") if result else ""
        # SenseVoice 输出带 <|zh|><|NEUTRAL|> 等标签，全部剥掉
        import re
        text = re.sub(r"<\|[^|]*\|>", "", text).strip()
        return {"text": text}
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("[ASR] 转写失败")
        raise HTTPException(500, f"转写失败: {exc}")
    finally:
        if tmp:
            Path(tmp.name).unlink(missing_ok=True)
