"""Pure fatigue advice policy."""

from server.compute.fatigue.types import FatigueAdvice, FatigueAssessment

def generate_fatigue_advice(assessment: FatigueAssessment | None) -> FatigueAdvice:
    if assessment is None:
        return FatigueAdvice(
            level="数据不足",
            text="行为数据不足，无法判断疲劳程度；其他可用指标仍已完成分析。",
        )
    probability = assessment.score_fraction
    if probability >= 0.75:
        level = "重度疲劳"
        advice = "请立即停止驾驶或高风险操作，离开屏幕并充分休息；若持续不适，请及时就医。"
    elif probability >= 0.5:
        level = "中度疲劳"
        advice = "建议暂停当前工作，闭目休息15至20分钟，并补充水分、适度活动。"
    elif probability >= 0.25:
        level = "轻度疲劳"
        advice = "建议短暂远眺和伸展，调整坐姿，并在下一工作周期安排休息。"
    else:
        level = "状态良好"
        advice = "当前未发现明显疲劳特征，请继续保持规律作息和适度休息。"
    return FatigueAdvice(level=level, text=advice)
