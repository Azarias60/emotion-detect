"""Public speech capability types consumed by report and observation flows.

None denotes normal missing data; typed detection errors propagate to the
caller. Audio input is a plain data value: audio arrives as PCM16 mono
little-endian bytes at the requested sample rate (``AudioData``); there is
no file or upload input on the active chain.
"""

from dataclasses import dataclass
from typing import Protocol

from server.compute.speech.types.results import Transcript, TranscriptSegment


@dataclass(frozen=True)
class AudioData:
    """一次音频输入：PCM16 单声道小端字节及其采样率。"""

    pcm: bytes
    sample_rate: int


class SpeechPort(Protocol):
    def result(self, audio: AudioData | None) -> Transcript | None: ...


__all__ = [
    "AudioData",
    "SpeechPort",
    "Transcript",
    "TranscriptSegment",
]
