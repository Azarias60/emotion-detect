"""现场录音的设备音频端口：从采集内核的同源音频缓冲切片。

start 记录起点，finish 取自起点到当前的同源音频切片；取消丢弃标记。
录音归属设备与语音能力的既有缓冲，不建立第二条采集链路。
"""
import time

from server.compute.speech.service import ASR_SAMPLE_RATE
from server.compute.speech.types import AudioData


class KernelAudioRecorder:
    """按账号记录起点并切片的录音端口；一次一段，未提交期间可取消。"""

    def __init__(self, kernel):
        self._kernel = kernel
        self._started_at: float | None = None

    def start(self):
        self._started_at = time.monotonic()
        return self._started_at

    def finish(self, handle):
        started = handle
        seconds = max(0.0, time.monotonic() - started)
        window = self._kernel.audio_window(seconds=seconds, sample_rate=ASR_SAMPLE_RATE)
        self._started_at = None
        if window is None:
            return AudioData(pcm=b"", sample_rate=ASR_SAMPLE_RATE)
        return AudioData(pcm=window.pcm, sample_rate=window.sample_rate)

    def cancel(self, handle):
        self._started_at = None


__all__ = ["KernelAudioRecorder"]
