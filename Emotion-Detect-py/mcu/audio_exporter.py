# -*-coding: utf-8 -*-
"""单片机音频导出、重采样与本地回放模块。"""
import os
import queue
import subprocess
import threading
import wave
from typing import List, Optional

try:
    import pyaudio
except ImportError:
    pyaudio = None

from core.config import AUDIO_CONFIG
from core.logger import get_logger

logger = get_logger(__name__)


def save_pcm_chunks_to_wav(chunks: List[bytes], temp_dir: str, base_filename: str = "mcu_audio") -> dict:
    """将单片机录制的 PCM 音频数据块保存为标准 WAV，并生成供 FunASR 使用的 16kHz WAV。"""
    if not chunks:
        return {"raw_wav": None, "asr_wav": None}

    raw_pcm = b"".join(chunks)
    wav_48k = os.path.join(temp_dir, f"{base_filename}_48k.wav")
    wav_16k = os.path.join(temp_dir, f"{base_filename}_16k.wav")

    # 1. 写入 48000Hz 16-bit 单声道 WAV
    try:
        with wave.open(wav_48k, "wb") as wf:
            wf.setnchannels(AUDIO_CONFIG["channels"])
            wf.setsampwidth(AUDIO_CONFIG["sample_width"])
            wf.setframerate(AUDIO_CONFIG["rate"])
            wf.writeframes(raw_pcm)
    except Exception as e:
        logger.error("写入原始音频失败：%s", e)
        return {"raw_wav": None, "asr_wav": None}

    # 2. 调用 ffmpeg 重采样为 16000Hz 单声道供语音转写与情感识别
    try:
        cmd = [
            "ffmpeg", "-y", "-i", wav_48k,
            "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1",
            wav_16k,
        ]
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, check=True)
        asr_target = wav_16k if os.path.exists(wav_16k) and os.path.getsize(wav_16k) > 0 else wav_48k
    except Exception as e:
        logger.warning("ffmpeg 音频重采样异常，使用原始音频：%s", e)
        asr_target = wav_48k

    return {"raw_wav": wav_48k, "asr_wav": asr_target}


class AudioPlayerWorker:
    """可选的本地音频实时监听/回放守护线程。"""

    def __init__(self, maxsize: int = 100):
        self._queue: queue.Queue = queue.Queue(maxsize=maxsize)
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

    def start(self):
        if pyaudio is None or (self._thread and self._thread.is_alive()):
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="MCU-Audio-Player")
        self._thread.start()

    def stop(self):
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)

    def put_chunk(self, pcm_chunk: bytes):
        if pyaudio is None:
            return
        if not self._queue.full():
            try:
                self._queue.put_nowait(pcm_chunk)
            except queue.Full:
                pass

    def _run(self):
        if pyaudio is None:
            return
        p = None
        stream = None
        try:
            p = pyaudio.PyAudio()
            stream = p.open(
                format=pyaudio.paInt16,
                channels=AUDIO_CONFIG["channels"],
                rate=AUDIO_CONFIG["rate"],
                output=True,
                frames_per_buffer=AUDIO_CONFIG["frames_per_buffer"],
            )
            while not self._stop_event.is_set():
                try:
                    chunk = self._queue.get(timeout=0.2)
                    if chunk:
                        stream.write(chunk)
                except queue.Empty:
                    continue
                except Exception:
                    break
        except Exception as e:
            logger.warning("PyAudio 扬声器回放初始化跳过：%s", e)
        finally:
            if stream:
                try:
                    stream.stop_stream()
                    stream.close()
                except Exception:
                    pass
            if p:
                try:
                    p.terminate()
                except Exception:
                    pass
