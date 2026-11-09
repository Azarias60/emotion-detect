"""语音转写与识别文本清洗，不依赖 HTTP 或全局模型。"""
import os
import re
from starlette.concurrency import run_in_threadpool

from core.logger import get_logger
from services.media_service import resolve_session_audio

logger = get_logger(__name__)


def split_and_clean_voice_by_two_sentences(res: list) -> list:
    """清洗 FunASR 识别结果并按短句合并。"""
    if not res or not res[0].get("text"):
        return []

    raw_text = res[0].get("text", "")
    clean_text = re.sub(r"<\|.*?\|>", "", raw_text).strip()

    if clean_text and not re.search(r"[。！？]", clean_text):
        return [{"text": clean_text, "emotion": "neutral"}]

    parts = re.split(r"([。！？])", clean_text)
    single = []
    for i in range(0, len(parts) - 1, 2):
        sentence = (parts[i] + parts[i + 1]).strip()
        if sentence:
            single.append(sentence)
    if len(parts) % 2 != 0 and parts[-1].strip():
        single.append(parts[-1].strip())

    return [{"text": " ".join(single[i : i + 2]), "emotion": "neutral"} for i in range(0, len(single), 2)]


async def transcribe_recorded_audio(temp_dir: str, *, voice_model, device) -> list:
    """导出并转写后端硬件录音为分句文本。"""
    if voice_model is None:
        return []
    audio_path = await run_in_threadpool(resolve_session_audio, temp_dir, device=device)
    if not audio_path or not os.path.exists(audio_path):
        return []
    try:
        res = await run_in_threadpool(voice_model.generate, input=audio_path, batch_size_s=300)
        return split_and_clean_voice_by_two_sentences(res)
    except Exception as error:
        logger.error("语音转写失败：%s", error)
        return []
