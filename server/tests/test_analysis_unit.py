# -*-coding: utf-8 -*-
"""P3 分析编排单元门禁：建议分级、测量包装、投影形状与 LLM 回退语义。

编排的端到端语义（409/部分失败/无回退）由 P4 迁移的契约测试覆盖，
此处钉死可独立验证的纯逻辑与异常映射。
"""
import pytest

from server.analysis.emotion.reporting.flow import extract_emotion_count_from_report
from server.compute.facephys.types import VideoHrvMeasurement


# ---- 独立测量 Result 与 Gateway 投影 ----
# 疲劳建议分级（阈值 0.75/0.5/0.25）的语义由其同域测试 test_fatigue_advice_module.py 覆盖。

def test_hrv_result_rejects_zero_rr_intervals_and_absent_indices_stay_none():
    result = VideoHrvMeasurement(
        sdnn_ms=22.0, rmssd_ms=18.0, rr_interval_count=3,
    )
    assert result.nn50 is None and result.pnn50_percent is None
    assert result.lf_hf_ratio is None
    with pytest.raises(ValueError, match="require RR"):
        VideoHrvMeasurement(sdnn_ms=22.0, rr_interval_count=0)


# ---- LLM 回退语义 ----

def test_extract_emotion_count():
    report = "分析正文\n喜怒哀虑统计：喜:3 怒:1 哀:2 虑:4"
    assert extract_emotion_count_from_report(report) == {"喜": 3, "怒": 1, "哀": 2, "虑": 4}
    assert extract_emotion_count_from_report("无统计行") == {"喜": 0, "怒": 0, "哀": 0, "虑": 0}


def test_emotion_questions_with_empty_model_response_use_question_bank(llm):
    from server.analysis.emotion.questions import (
        EmotionQuestionPreparationService, LLMEmotionQuestionTextReader, DEFAULT_PSYCHOLOGY_QUESTIONS,
    )

    llm("")
    from server.analysis.llm_client import create_client
    assert EmotionQuestionPreparationService(
        LLMEmotionQuestionTextReader(create_client)).result().questions == DEFAULT_PSYCHOLOGY_QUESTIONS
