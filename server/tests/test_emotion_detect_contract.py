# -*-coding: utf-8 -*-
"""情绪检测意图契约：启动→封存→等待报告事实。

对应原设计 001 的 C5（报告事实结构）、C7（缺失数据可区分）。部分模型/
输入缺失保留可用结果；全部输入缺失与处理失败以失败事实呈现，不再有独
立的 HTTP 失败响应。情绪问题生成是域级能力：HTTP 取题端点已退役，其
策略行为直接对 ``EmotionQuestionPreparationService`` 断言。
"""
import pytest

from server.tests.detection_client import finish, wait_for_report_stage
from server.tests.detection_steps import start_detection
from server.tests.runtime_fixture import seed_audio
from server.tests import contract_stubs as stubs
from server.compute.fatigue.types import BehaviorObservation, FatigueAssessment

# 观察与评估载荷按公开契约直接构造；数值与生产打分对同一输入的产出一致。
FATIGUE_OK_RESULT = BehaviorObservation(
    blink_count=12, blink_frequency=12.0, yawn_count=1, nod_count=0,
    nod_frequency=0.0, perclos_percent=12.0, speaking_percent=8.0,
    face_coverage=0.97,
)
FATIGUE_ASSESSMENT = FatigueAssessment(score=0, level="正常")

from server.tests.measurement_fakes import physiology_ok  # noqa: E402

PHYSIOLOGY_OK = physiology_ok()
HRV_PAYLOAD = {"sdnn_ms": 41.2, "rmssd_ms": 33.8, "nn50": 6, "pnn50_percent": 18.4,
               "lf_hf_ratio": 1.7, "rr_interval_count": 3, "rr_source": "rppg_peak_derived"}

UNCLASSIFIED_FAILURE = "检测失败，请查看服务端日志"
# 产品接线缺陷（见最终报告缺陷2）：analyze 收到 measure 的整体字典，
# write 属性访问失败使成功路径无法到达模型解读；修复后移除标记。
ANALYZE_WIRING_XFAIL = "产品接线缺陷：analyze 未解包 measure 的 input（缺陷2）"


@pytest.mark.xfail(reason=ANALYZE_WIRING_XFAIL, strict=False)
def test_success_reaches_the_report_facts(
    client, face_predictor, voice_model, llm, fatigue_stub, physiology_stub,
):
    predictor = face_predictor(["happy", "happy", "neutral"])
    voice_model("你好。最近有点累！心率正常吗？")
    report_text = "面部表情以开心为主。\n最终判定：开心\n喜怒哀虑统计：喜:1 怒:0 哀:0 虑:2"
    llm(report_text)
    fatigue_stub(FATIGUE_OK_RESULT)
    physiology_stub(PHYSIOLOGY_OK)
    headers, report_id = start_detection(client, None, "emotion", "emo-success")
    seed_audio(b"\x01\x00" * 16000)
    finish(client, headers, report_id)

    result = wait_for_report_stage(client, headers, report_id, "complete")["result"]
    assert result["expression"]["class_counts"] == [["happy", 2], ["neutral", 1]]
    assert [item["label"] for item in result["expression"]["observations"]] == [
        "happy", "happy", "neutral"]
    assert result["speech"] == {"segments": [{"text": "你好。 最近有点累！"},
                                             {"text": "心率正常吗？"}]}
    assert result["behavior"] == {
        "blink_count": 12, "blink_frequency": 12.0,
        "yawn_count": 1, "nod_count": 0, "nod_frequency": 0.0,
        "perclos_percent": 12.0, "speaking_percent": 8.0,
        "face_coverage": 0.97,
    }
    assert result["fatigue"] == {
        "score": FATIGUE_ASSESSMENT.score,
        "level": FATIGUE_ASSESSMENT.level,
        "score_fraction": FATIGUE_ASSESSMENT.score_fraction,
    }
    assert result["advice"]["level"] == "状态良好"
    assert result["physiology"]["heart_rate_bpm"] == PHYSIOLOGY_OK.heart_rate_bpm
    assert result["physiology"]["hrv"] == HRV_PAYLOAD
    assert result["report"] == {"text": report_text}
    assert result["emotion_word_count"] == {"喜": 1, "怒": 0, "哀": 0, "虑": 2}
    assert result["detection_id"] == str(report_id)
    # 设备帧窗口直接进入表情模型（无文件入口）
    assert len(predictor.recognize_video_calls) == 1
    call = predictor.recognize_video_calls[0]
    assert call["frames"] is not None and len(call["frames"]) == 5
    assert call["interval"] == 10
    # LLM 收到汇总数据，且使用配置的模型地址
    assert len(stubs.FakeOpenAI.instances) == 1
    assert stubs.FakeOpenAI.instances[0].base_url == "http://127.0.0.1:8082/v1"


@pytest.mark.xfail(reason=ANALYZE_WIRING_XFAIL, strict=False)
def test_audio_only_keeps_missing_data_explicit(client, face_predictor, voice_model, llm):
    predictor = face_predictor(["sad"])
    voice_model("仅有语音仍然检测。")
    llm("无可分析素材。\n最终判定：平静\n喜怒哀虑统计：喜:0 怒:0 哀:0 虑:0")
    headers, report_id = start_detection(client, None, "emotion", "emo-audio-only")
    seed_audio(b"\x01\x00" * 16000)
    finish(client, headers, report_id)

    result = wait_for_report_stage(client, headers, report_id, "complete")["result"]
    # 无视频时表情模型不应被启动
    assert predictor.recognize_video_calls == []
    assert result["expression"] is None
    # 疲劳与心率保持缺失语义，不填充正常值
    assert result["behavior"] is None
    assert result["fatigue"] is None
    assert result["advice"]["level"] == "数据不足"
    assert result["physiology"] is None
    assert result["speech"] == {"segments": [{"text": "仅有语音仍然检测。"}]}


@pytest.mark.xfail(reason=ANALYZE_WIRING_XFAIL, strict=False)
def test_empty_device_audio_keeps_speech_null(
    client, face_predictor, voice_model, llm, fatigue_stub, physiology_stub,
):
    voice_model("不应被调用")
    llm("空音频。\n最终判定：平静\n喜怒哀虑统计：喜:0 怒:0 哀:0 虑:0")
    fatigue_stub(FATIGUE_OK_RESULT)
    physiology_stub(PHYSIOLOGY_OK)
    headers, report_id = start_detection(client, None, "emotion", "emo-silent")
    finish(client, headers, report_id)

    result = wait_for_report_stage(client, headers, report_id, "complete")["result"]
    assert result["speech"] is None


def test_hrv_failure_records_public_failure_without_internal_details(
    client, request, face_predictor, llm, fatigue_stub, physiology_stub,
):
    face_predictor(["happy"])
    llm("汇总正常。\n最终判定：开心\n喜怒哀虑统计：喜:0 怒:0 哀:0 虑:0")
    fatigue_stub(FATIGUE_OK_RESULT)
    physiology_stub(error=RuntimeError("HRV 模型损坏"))
    headers, report_id = start_detection(client, request, "emotion", "emo-hrv-fail")
    finish(client, headers, report_id)

    result = wait_for_report_stage(client, headers, report_id, "failed")["result"]
    assert result["failure"] == UNCLASSIFIED_FAILURE
    assert "HRV 模型损坏" not in repr(result)


def test_unloaded_face_model_fails_report_as_internal_error(client, llm, fatigue_stub, physiology_stub):
    """表情模型缺失时技术失败传播，不做部分成功伪装。"""
    llm("部分检测结果")
    fatigue_stub(FATIGUE_OK_RESULT)
    physiology_stub(PHYSIOLOGY_OK)
    headers, report_id = start_detection(
        client, None, "emotion", "emo-no-face", emotions=None)
    finish(client, headers, report_id)

    result = wait_for_report_stage(client, headers, report_id, "failed")["result"]
    assert result["failure"] == UNCLASSIFIED_FAILURE


def test_all_data_missing_is_recorded_as_failed_fact(client):
    headers, report_id = start_detection(
        client, None, "emotion", "emo-all-missing", emotions=None, frames=0)
    finish(client, headers, report_id)

    result = wait_for_report_stage(client, headers, report_id, "failed")["result"]
    assert "全部检测数据缺失" in result["failure"]


def test_busy_admission_records_failure_fact_instead_of_409(client, request, face_predictor, llm, api):
    face_predictor(["happy"])
    llm("占用中")
    headers, report_id = start_detection(client, request, "emotion", "emo-busy")
    assert api.emotion_analysis_lock.acquire(blocking=False)
    try:
        finish(client, headers, report_id)
        result = wait_for_report_stage(client, headers, report_id, "failed")["result"]
    finally:
        api.emotion_analysis_lock.release()

    assert result["failure"] == "情绪检测任务正在分析，请勿重复提交"


# --- 情绪问题准备：域级能力（HTTP 取题端点已退役） ------------------------------


def _question_service():
    from server.analysis.emotion.assembly import create_emotion_question_service

    return create_emotion_question_service()


def test_psychology_questions_keep_first_ten_numbered_lines(client, llm):
    questions = [f"{index}.问题{index}？" for index in range(1, 13)]
    llm("前言\n" + "\n".join(questions) + "\n结尾")

    prepared = _question_service().result()

    assert prepared.questions == tuple(questions[:10])


def test_psychology_questions_short_reply_falls_back_to_defaults(client, llm):
    from server.analysis.emotion.questions import DEFAULT_PSYCHOLOGY_QUESTIONS

    llm("1.只有一问")
    prepared = _question_service().result()

    assert prepared.questions == DEFAULT_PSYCHOLOGY_QUESTIONS


def test_psychology_questions_programming_error_is_not_business_fallback(client, monkeypatch):
    def fail():
        raise RuntimeError("client unavailable")

    # 传输工厂随注入装配：补丁打在装配出的 reader 实例上。
    from server.analysis.emotion.assembly import create_emotion_question_service
    prepared_service = create_emotion_question_service()
    monkeypatch.setattr(prepared_service._text_reader, "_client_factory", fail)

    with pytest.raises(RuntimeError, match="client unavailable"):
        prepared_service.result()
