"""Local Windows SAPI voice used only when the online TTS keeps failing.

Speaks through PowerShell's System.Speech synthesis into a WAV, then returns
16 kHz mono PCM. Zero network, zero model: it exists so a bad Edge hour never
silences whole replies on the lightweight page.
"""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path
from typing import Iterator

import numpy as np
import soundfile as sf

from utils.logger import logger

VOICE = "Microsoft Huihui Desktop"  # 本地中文女声

_PS_TEMPLATE = r"""
Add-Type -AssemblyName System.Speech
$s = New-Object System.Speech.Synthesis.SpeechSynthesizer
$s.SelectVoice('{voice}')
$s.SetOutputToWaveFile('{path}')
$s.Speak('{text}')
$s.Dispose()
"""


def synthesize_to_pcm(text: str, sample_rate: int = 16000) -> np.ndarray:
    """Synthesize `text` offline; returns float32 PCM at sample_rate."""
    import resampy

    safe = text.replace("'", "''")
    with tempfile.TemporaryDirectory() as tmp:
        wav = Path(tmp) / "tts.wav"
        script = _PS_TEMPLATE.format(voice=VOICE, path=wav, text=safe)
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-Command", script],
            capture_output=True,
            timeout=20,
        )
        if proc.returncode != 0 or not wav.is_file():
            raise RuntimeError(
                f"Windows 语音合成失败: {proc.stderr.decode(errors='ignore')[:200]}"
            )
        data, sr = sf.read(str(wav), dtype="float32")
        if data.ndim > 1:
            data = data[:, 0]
        if sr != sample_rate and data.size:
            data = resampy.resample(data, sr, sample_rate)
        return np.asarray(data, dtype=np.float32)


class WindowsTTSVoice:
    """Same ``_generate(text)`` generator contract as the other voices."""

    def __init__(self, opt=None):
        self.opt = opt

    def _generate(self, text: str) -> Iterator[np.ndarray]:
        pcm = synthesize_to_pcm(text)
        if pcm.size:
            yield pcm
        else:
            logger.warning("[WindowsTTS] 空音频输出")
