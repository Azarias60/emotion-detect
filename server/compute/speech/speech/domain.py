"""Pure transcript segmentation policy, independent of ASR engines and media I/O.

The result payloads are published from the domain contracts; this module keeps
the existing split/whitespace behavior, including preserving a final sentence
without terminal punctuation.
"""

import re

from server.compute.speech.types import Transcript, TranscriptSegment


def segment_transcript(raw_text: str) -> tuple[TranscriptSegment, ...]:
    """Remove SenseVoice control tags and group Chinese sentences in pairs."""
    clean_text = re.sub(r'<\|.*?\|>', '', raw_text).strip()
    if clean_text and not re.search(r'[。！？]', clean_text):
        return (TranscriptSegment(clean_text),)

    parts = re.split(r'([。！？])', clean_text)
    single = []
    for i in range(0, len(parts) - 1, 2):
        sentence = (parts[i] + parts[i + 1]).strip()
        if sentence:
            single.append(sentence)
    if len(parts) % 2 != 0 and parts[-1].strip():
        single.append(parts[-1].strip())

    return tuple(
        TranscriptSegment(' '.join(single[i:i + 2]))
        for i in range(0, len(single), 2)
    )
