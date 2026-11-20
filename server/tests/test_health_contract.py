# -*-coding: utf-8 -*-
"""健康问诊意图契约：启动→望诊事实/题目→逐题问答→封存→报告事实。

对应原设计 001 的 C4（逐题答案由后端从封存音频转写并参与报告）、C7（缺失
数据）。望诊事实与题目在启动准备里写入报告；逐题答案由展示反馈驱动的
问答写入；封存后的测量/解读失败以报告事实呈现。
"""
import json
import time

import pytest

from server.tests.detection_client import (
    auth_headers, begin, cancel, finish, report, wait_for_report_stage,
)
from server.tests.runtime_fixture import attach_fake_device, seed_audio

UNCLASSIFIED_FAILURE = "检测失败，请查看服务端日志"
# 产品接线缺陷（见最终报告缺陷2）：analyze 未解包 measure 的 input，健康
# 解读路径在 write 入口即失败；依赖解读产出的断言修复后移除标记。
ANALYZE_WIRING_XFAIL = "产品接线缺陷：analyze 未解包 measure 的 input（缺陷2）"
# 产品接线缺陷（见最终报告缺陷1）：create_measure 返回的事实未被调度记录。
MEASURE_FACTS_XFAIL = "产品接线缺陷：measure 事实未被调度写入报告（缺陷1）"


def wait_for_fact(client, headers, report_id, key, timeout=10.0):
    """轮询报告事实，等待问答/指标等异步事实出现。"""
    deadline = time.monotonic() + timeout
    data = report(client, headers, report_id)
    while time.monotonic() < deadline:
        if key in data["result"]:
            return data
        time.sleep(0.1)
        data = report(client, headers, report_id)
    raise AssertionError(f"报告未在时限内出现事实 {key!r}：{data['result']!r}")


def _begin_health(client, request, username, *, frames=5):
    from server.tests.detection_steps import start_detection

    return start_detection(client, request, "health", username, frames=frames)


def test_tongue_facts_and_questions_reach_the_report(client, request):
    headers, report_id = _begin_health(client, request, "hc-tongue", frames=0)

    result = report(client, headers, report_id)["result"]
    tongue = result["tongue"]
    assert set(tongue) == {"quality", "recognition", "constitution", "constitution_report"}
    # 质量检查是真实图像处理；标签映射与体质规则是真实业务代码。
    assert tongue["quality"]["passed"] is True
    assert tongue["quality"]["warnings"] == []
    assert "brightness" in tongue["quality"] and "sharpness" in tongue["quality"]
    assert tongue["recognition"]["color"] == {
        "class_name": "red", "probability": 0.91, "label": "淡红舌",
    }
    assert tongue["recognition"]["indentation"] == {
        "class_name": "indentation", "probability": 0.66, "label": "齿痕舌",
    }
    assert tongue["constitution"]["candidates"], "体质候选 Result 不应为空"
    assert tongue["constitution_report"]["constitution_name"], "体质报告 Result 不应为空"
    # 题目与本次舌象同源：题干材料取自本次识别产物。
    questions = result["questions"]
    assert questions and all(isinstance(question, str) for question in questions)
    assert "白苔" in questions[0] and "齿痕舌" in questions[3]
    # 本例只验证启动准备事实；结束本轮，避免问答轮次泄漏到共享装配。
    cancel(client, headers, report_id)


def test_displayed_feedback_drives_one_real_answer_round(client, request, voice_model):
    headers, report_id = _begin_health(client, request, "hc-answers")
    voice_model("睡眠不太好")
    questions = report(client, headers, report_id)["result"]["questions"]
    # 展示确认先行：问答切片只消费确认之后的音频。
    displayed = client.post(
        f"/api/v1/detections/{report_id}/questions/displayed", json={"index": 0},
        headers=headers)
    assert displayed.status_code == 200, displayed.text
    seed_audio(b"\x01\x00" * 16000)

    result = wait_for_fact(client, headers, report_id, "answers")["result"]
    # 展示确认后的 3 秒首转写节奏内完成一次真实问答。
    assert result["answers"] == [{"question": questions[0], "answer": "睡眠不太好"}]
    cancel(client, headers, report_id)


@pytest.mark.xfail(reason=ANALYZE_WIRING_XFAIL, strict=False)
def test_answers_enter_the_llm_context(client, request, voice_model, llm, fatigue_stub, physiology_stub):
    llm("根据问答生成的报告")
    fatigue_stub()
    physiology_stub()
    headers, report_id = _begin_health(client, request, "hc-context")
    voice_model("最近睡不着，早上很累")
    questions = report(client, headers, report_id)["result"]["questions"]
    client.post(f"/api/v1/detections/{report_id}/questions/displayed",
                json={"index": 0}, headers=headers)
    seed_audio(b"\x01\x00" * 16000)
    wait_for_fact(client, headers, report_id, "answers")
    finish(client, headers, report_id)

    result = wait_for_report_stage(client, headers, report_id, "complete")["result"]
    assert result["answers"] == [{"question": questions[0], "answer": "最近睡不着，早上很累"}]
    assert result["speech"] == {"segments": [{"text": "最近睡不着，早上很累"}]}
    assert result["report"] == {"text": "根据问答生成的报告"}
    # 逐题答案与整段转写分离进入报告上下文（C4）
    sent = stubs_first_call()
    payload = json.loads(sent["messages"][1]["content"])
    assert payload["answers"] == result["answers"]
    assert payload["speech"] == result["speech"]
    assert "不得自行配给问题" in sent["messages"][0]["content"]


def stubs_first_call():
    from server.tests import contract_stubs as stubs

    return stubs.FakeOpenAI.instances[0].calls[0]


@pytest.mark.xfail(reason=ANALYZE_WIRING_XFAIL, strict=False)
def test_report_without_api_key_omits_authorization(client, request, llm, monkeypatch):
    llm("无密钥端点的报告。")
    headers, report_id = _begin_health(client, request, "hc-anon")
    from openai import omit
    from server import settings

    monkeypatch.setitem(settings.AI_CONFIG, "api_key", "")
    finish(client, headers, report_id)

    result = wait_for_report_stage(client, headers, report_id, "complete")["result"]
    assert result["report"] == {"text": "无密钥端点的报告。"}
    assert stubs_first_call()["extra_headers"] == {"Authorization": omit}


@pytest.mark.xfail(reason=MEASURE_FACTS_XFAIL, strict=False)
def test_audio_without_video_keeps_missing_semantics_explicit(
    client, request, voice_model, llm,
):
    voice_model("睡眠不太好")
    llm("根据转写生成的报告")
    headers, report_id = _begin_health(client, request, "hc-audio", frames=0)
    seed_audio(b"\x01\x00" * 16000)
    finish(client, headers, report_id)

    result = wait_for_report_stage(client, headers, report_id, "complete")["result"]
    assert result["speech"] == {"segments": [{"text": "睡眠不太好"}]}
    # 无视频时行为、疲劳与心率保持缺失语义（C7）
    assert result["behavior"] is None
    assert result["fatigue"] is None
    assert result["advice"]["level"] == "数据不足"
    assert result["physiology"] is None
    assert result["report"] == {"text": "根据转写生成的报告"}


def test_malformed_body_is_framework_validation(client):
    from server.tests.detection_client import auth_headers

    response = client.post(
        "/api/v1/detections",
        content="{bad json",
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 422
    assert isinstance(response.json()["detail"], list)

    undeclared = client.post(
        "/api/v1/detections",
        json={"kind": "health", "preparation": {"tongue": {"quality": "[]"}}},
        headers=auth_headers(client, "hc-shape"),
    )
    assert undeclared.status_code == 422
    assert isinstance(undeclared.json()["detail"], list)


def test_busy_admission_records_failure_fact_instead_of_409(client, request, api):
    headers, report_id = _begin_health(client, request, "hc-busy")
    from server.tests.lease_view import LeaseView
    from server.tests.runtime_fixture import assembly
    lock = LeaseView(assembly.admission)
    assert lock.acquire(blocking=False)
    try:
        finish(client, headers, report_id)
        result = wait_for_report_stage(client, headers, report_id, "failed")["result"]
    finally:
        lock.release()

    assert result["failure"] == "健康问诊任务正在分析，请勿重复提交"
