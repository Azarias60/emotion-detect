import pytest

from server.compute.fatigue.types import FatigueAdvice
from server.compute.fatigue.fatigue_advice.domain import generate_fatigue_advice
from server.compute.fatigue.types import FatigueAssessment

INSUFFICIENT_TEXT = "行为数据不足，无法判断疲劳程度；其他可用指标仍已完成分析。"


def test_advice_returns_data_insufficient_without_assessment():
    assert generate_fatigue_advice(None) == FatigueAdvice(
        level="数据不足",
        text=INSUFFICIENT_TEXT,
    )


@pytest.mark.parametrize("score,expected_level,expected_text", [
    (
        4, "重度疲劳",
        "请立即停止驾驶或高风险操作，离开屏幕并充分休息；若持续不适，请及时就医。",
    ),
    (
        3, "重度疲劳",
        "请立即停止驾驶或高风险操作，离开屏幕并充分休息；若持续不适，请及时就医。",
    ),
    (
        2, "中度疲劳",
        "建议暂停当前工作，闭目休息15至20分钟，并补充水分、适度活动。",
    ),
    (
        1, "轻度疲劳",
        "建议短暂远眺和伸展，调整坐姿，并在下一工作周期安排休息。",
    ),
    (
        0, "状态良好",
        "当前未发现明显疲劳特征，请继续保持规律作息和适度休息。",
    ),
])
def test_advice_text_is_unchanged_for_every_threshold(score, expected_level, expected_text):
    advice = generate_fatigue_advice(FatigueAssessment(score=score, level="正常"))
    assert (advice.level, advice.text) == (expected_level, expected_text)
