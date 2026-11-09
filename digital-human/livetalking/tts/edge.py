"""Streaming Edge TTS voice for the lightweight default page.

Follows the upstream LiveTalking ``tts/edge.py`` approach: collect the full
mp3 from one ``Communicate.stream()`` call, then decode it in-process with
soundfile and resample to 16 kHz. No ffmpeg subprocess per segment.
"""

from __future__ import annotations

import asyncio
import time
from io import BytesIO
from typing import Iterator

import numpy as np

from utils.logger import logger

DEFAULT_VOICE = "zh-CN-YunxiNeural"  # 活泼少年男声


class EdgeTTSVoice:
    """Same ``_generate(text)`` generator contract as CosyVoice3TTS."""

    def __init__(self, opt):
        self.voice = getattr(opt, "default_tts_voice", "") or DEFAULT_VOICE

    def _collect_mp3(self, text: str) -> bytes:
        async def collect() -> bytes:
            import edge_tts

            buffer = bytearray()
            communicate = edge_tts.Communicate(text, self.voice)
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    buffer.extend(chunk["data"])
            return bytes(buffer)

        return asyncio.run(collect())

    def _generate(self, text: str) -> Iterator[np.ndarray]:
        started = time.perf_counter()
        mp3 = self._collect_mp3(text)
        if not mp3:
            # Empty service response; let the caller retry or fall back.
            raise RuntimeError("Edge TTS 无音频输出")
        import resampy
        import soundfile as sf

        data, sample_rate = sf.read(BytesIO(mp3), dtype="float32")
        if data.ndim > 1:
            data = data[:, 0]
        if sample_rate != 16000 and data.size:
            data = resampy.resample(data, sample_rate, 16000)
        logger.info(
            "Edge TTS 合成完成: %.2fs 音频, 耗时 %.2fs",
            data.size / 16000,
            time.perf_counter() - started,
        )
        yield np.asarray(data, dtype=np.float32)
