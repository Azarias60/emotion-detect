"""Public speech entry: model ownership and PCM transcription.

本域持有驻留模型与推理锁（``load``/``close`` 由装配根驱动）。识别管线
只消费 16kHz/单声道/PCM16 小端字节：调用方（网关单项操作或综合分析
流程）从采集快照展开为 :class:`AudioData` 传入，FunASR 以
``input_format="pcm_s16le"`` 直接解码。缺失音频为 None；模型不可用抛
类型化失败。实时采集处理与缓冲在 capture 中保持 48kHz。

模型装配知识不在本模块：``load`` 调用装配时注入的模型工厂，
SDK 与权重的导入点保持在 ``adapters/``。
"""
import threading
from collections.abc import Callable

from server.compute.speech.types import AudioData, Transcript
from server.compute.speech.speech.application import transcribe_pcm

ASR_SAMPLE_RATE = 16000


class SpeechService:
    """语音域资源所有者：驻留模型、推理锁与 PCM 转写入口。

    模型槽位 ``model`` 由装配根的 ``load``/``close`` 驱动；测试替身直接
    替换该槽位，不经过全局注册表。
    """

    def __init__(self, model_factory: Callable[[], object]):
        self._model_factory = model_factory
        self.model = None
        self.model_lock = threading.Lock()

    def load(self) -> None:
        self.model = self._model_factory()

    def close(self) -> None:
        with self.model_lock:
            self.model = None

    def result(self, audio: AudioData | None) -> Transcript | None:
        """Transcribe the given PCM data; propagate technical failures."""
        if audio is None or not audio.pcm:
            return None
        with self.model_lock:
            if self.model is None:
                raise RuntimeError("SpeechService is not loaded")
            return transcribe_pcm(self.model, audio.pcm, audio.sample_rate)


__all__ = [
    "ASR_SAMPLE_RATE",
    "SpeechService",
]
