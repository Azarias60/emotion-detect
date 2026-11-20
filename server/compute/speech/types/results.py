"""Public speech result payloads owned by the speech compute domain.

The current ASR adapter supplies text only. No timestamps, confidence, or
speech-emotion predictions are available from this result contract. Absent
transcript data is represented by None at the observation boundary.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class TranscriptSegment:
    text: str


@dataclass(frozen=True)
class Transcript:
    """Transcript payload of speech recognition."""

    segments: tuple[TranscriptSegment, ...]

    @property
    def text(self) -> str:
        return " ".join(segment.text for segment in self.segments)
