"""Pure segmentation and transcript payload contracts without ASR frameworks."""

from dataclasses import FrozenInstanceError

import pytest

from server.compute.speech.types import Transcript, TranscriptSegment
from server.compute.speech.speech.domain import segment_transcript


@pytest.mark.parametrize(
    "raw_text,expected",
    [
        ("<|zh|><|HAPPY|>今天天气很好。心情不错。", ("今天天气很好。 心情不错。",)),
        ("<|en|>hello world", ("hello world",)),
        ("甲。乙。丙！", ("甲。 乙。", "丙！")),
        ("甲。乙。丙", ("甲。 乙。", "丙")),
        ("<|en|><|NEUTRAL|>", ()),
        ("", ()),
        ("  ", ()),
        ("你好！！再见？", ("你好！ ！", "再见？")),
    ],
)
def test_pure_segmentation_contract(raw_text, expected):
    result = segment_transcript(raw_text)
    assert isinstance(result, tuple)
    assert all(isinstance(segment, TranscriptSegment) for segment in result)
    assert tuple(segment.text for segment in result) == expected


def test_segment_is_immutable_and_has_no_fabricated_emotion():
    segment = segment_transcript("hello")[0]
    assert not hasattr(segment, "emotion")
    assert not hasattr(segment, "confidence")
    with pytest.raises(FrozenInstanceError):
        segment.text = "modified"


def test_transcript_payload_carries_segments_without_status():
    transcript = Transcript((TranscriptSegment("hello"), TranscriptSegment("world")))
    assert tuple(segment.text for segment in transcript.segments) == ("hello", "world")
    assert not hasattr(transcript, "status")
    with pytest.raises(FrozenInstanceError):
        transcript.segments = ()
