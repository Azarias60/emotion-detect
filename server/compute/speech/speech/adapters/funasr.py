"""FunASR adapter: the only place the speech domain touches the SDK and weights."""


class FunASRTextReader:
    """Translate the model-specific generate payload into raw recognized text.

    输入为 PCM16 单声道小端字节；``input_format="pcm_s16le"`` 让 FunASR
    直接按字节解码（与读取 WAV 文件得到同一 float32 样本序列）。
    """

    def __init__(self, model):
        self._model = model

    def recognize(self, pcm: bytes, sample_rate: int = 16000) -> str | None:
        result = self._model.generate(
            input=pcm, input_format="pcm_s16le", fs=sample_rate,
            language="auto", use_itn=True,
        )
        return result[0]["text"] if result else None


def load_voice_model(device: str):
    """FunASR SenseVoice 惰性装配；必需依赖和模型文件缺失直接中止启动。"""
    from funasr import AutoModel
    from server import settings
    return AutoModel(
        model=settings.VOICE_MODEL_DIR,
        vad_model=settings.VOICE_VAD_DIR,
        device=device,
        disable_update=True,
    )
