"""Speech model binding assembly; imported only by the assembly root."""

from server.compute.speech.service import SpeechService
from server.compute.speech.speech.adapters.funasr import load_voice_model


def create_speech_service() -> SpeechService:
    """组合语音域服务；模型槽位与锁由返回的服务自身持有。"""
    return SpeechService(lambda: load_voice_model(_resolve_asr_device()))


__all__ = ["create_speech_service", "load_voice_model"]


def _resolve_asr_device() -> str:
    """语音模型运行设备：settings.ASR_DEVICE；auto 时按 CUDA 可用性回退 CPU。"""
    from server import settings

    if settings.ASR_DEVICE and settings.ASR_DEVICE != "auto":
        return settings.ASR_DEVICE
    import torch
    return "cuda:0" if torch.cuda.is_available() else "cpu"
