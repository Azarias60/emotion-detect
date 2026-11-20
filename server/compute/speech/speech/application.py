"""Speech transcription pipeline: resident model, raw text, typed transcript.

FunASR 读取器是文本读取缝口的唯一实现，用例不再单设端口协议；测试替身
直接替换模型槽位。
"""

from server.compute.speech.types import Transcript
from server.compute.speech.speech.adapters.funasr import FunASRTextReader
from server.compute.speech.speech.domain import segment_transcript


def transcribe_pcm(model, pcm: bytes, sample_rate: int = 16000) -> Transcript | None:
    raw_text = FunASRTextReader(model).recognize(pcm, sample_rate)
    segments = segment_transcript(raw_text) if raw_text else ()
    return Transcript(segments=segments) if segments else None
