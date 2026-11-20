# -*-coding: utf-8 -*-
"""健康启动的望诊契约：舌象事实与本地题目进入报告；准备失败按意图错误面响应。

质量门→识别→体质→体质报告的域级行为由 ``test_tongue_observation_service.py``
覆盖；这里只断言一次望诊经启动写入报告事实，以及望诊失败的新错误面
（准备失败 → 检测域错误响应，报告被移除）。
"""
import numpy as np
import pytest

from server.tests.detection_client import auth_headers, cancel
from server.tests.runtime_fixture import attach_fake_device, seed_frame


def _passing_frame():
    # 240×320 且带对比区域：分辨率、亮度与清晰度全部通过质量阈值。
    frame = np.full((240, 320, 3), 160, dtype=np.uint8)
    frame[10:40, 10:60] = 40
    return frame


def _start_health(client, username="tongue-ct"):
    attach_fake_device()
    headers = auth_headers(client, username)
    response = client.post("/api/v1/detections", json={"kind": "health"}, headers=headers)
    return headers, response


def test_health_start_persists_tongue_facts_and_local_questions(client, tongue_model):
    tongue_model()
    seed_frame(_passing_frame())
    headers, response = _start_health(client)
    assert response.status_code == 200, response.text
    report_id = response.json()["data"]["reportId"]

    from server.tests.detection_client import report

    result = report(client, headers, report_id)["result"]
    tongue = result["tongue"]
    assert set(tongue) == {
        "quality", "recognition", "constitution", "constitution_report",
    }
    # 标签映射与体质规则是真实业务代码
    assert tongue["recognition"]["color"] == {
        "class_name": "red", "probability": 0.91, "label": "淡红舌",
    }
    assert tongue["recognition"]["indentation"] == {
        "class_name": "indentation", "probability": 0.66, "label": "齿痕舌",
    }
    assert tongue["constitution"]["candidates"], "体质候选 Result 不应为空"
    assert tongue["constitution_report"]["constitution_name"], "体质报告 Result 不应为空"
    # 题目与本次舌象同源：未配 AI 时落到据识别产物生成的本地问题
    questions = result["questions"]
    assert questions and all(isinstance(question, str) for question in questions)
    assert "白苔" in questions[0] and "齿痕舌" in questions[3]
    cancel(client, headers, report_id)  # 结束本轮：问答轮次不泄漏到共享装配
    # 质量检查是真实图像处理
    assert tongue["quality"]["passed"] is True
    assert tongue["quality"]["warnings"] == []
    assert "brightness" in tongue["quality"] and "sharpness" in tongue["quality"]


def test_predictor_error_fails_start_and_removes_the_report(client, tongue_model):
    from server.tests.contract_stubs import StubTonguePredictorError

    tongue_model(error=StubTonguePredictorError("权重加载失败：C:/models/secret.pt"))
    seed_frame(_passing_frame())
    headers, response = _start_health(client)

    # 未分类域失败固定 500 公开文案（语义由 DetectionFlowError.__str__ 收敛）；
    # 准备失败的报告不残留：启动中止会移除本轮报告。
    assert response.status_code == 500
    assert response.json()["detail"] == "检测失败，请查看服务端日志"
    assert "secret.pt" not in response.text
    assert client.get("/api/v1/reports", headers=headers).json()["data"]["items"] == []


def test_model_load_error_during_inference_is_internal_failure(client, tongue_model):
    from server.compute.tongue.tongue_recognition.errors import ModelLoadError

    tongue_model(error=ModelLoadError("模型加载失败: C:/models/secret.pt"))
    seed_frame(_passing_frame())
    _, response = _start_health(client)

    assert response.status_code == 500
    assert response.json()["detail"] == "检测失败，请查看服务端日志"
    assert "secret.pt" not in response.text


def test_unloaded_model_is_internal_programming_error(client):
    seed_frame(_passing_frame())
    _, response = _start_health(client)

    assert response.status_code == 500
    assert response.text == "Internal Server Error"


def test_start_without_frame_is_an_input_error(client, tongue_model):
    tongue_model()
    _, response = _start_health(client)

    assert response.status_code == 400
    assert response.json()["detail"] == "当前没有可用摄像头画面"


def test_low_quality_frame_stops_follow_up_capabilities(client, tongue_model):
    stub = tongue_model()
    seed_frame(np.full((48, 64, 3), 120, dtype=np.uint8))
    headers, response = _start_health(client)

    assert response.status_code == 200, response.text
    report_id = response.json()["data"]["reportId"]
    assert stub.predict_calls == [], "质量不过关不进识别链"
    from server.tests.detection_client import report

    tongue = report(client, headers, report_id)["result"]["tongue"]
    assert tongue["quality"]["passed"] is False
    assert tongue["recognition"] is None
    assert tongue["constitution"] is None
    assert tongue["constitution_report"] is None
    # 质量不过关就不进识别链；取题递 None，命题层只给不凭空的通用题
    questions = report(client, headers, report_id)["result"]["questions"]
    assert questions and all(isinstance(question, str) for question in questions)
    assert not any(feature in question
                   for question in questions
                   for feature in ("白苔", "黄苔", "齿痕舌", "淡红舌", "裂纹"))
    cancel(client, headers, report_id)


def test_service_load_preserves_original_model_load_error(monkeypatch):
    from server.compute.tongue.tongue_recognition.errors import ModelLoadError
    from server.tests.runtime_fixture import assembly

    failure = ModelLoadError("模型加载失败: C:/models/secret.pt")

    def broken():
        raise failure

    monkeypatch.setattr(assembly.tongue, "_predictor_factory", broken)
    with pytest.raises(ModelLoadError) as caught:
        assembly.tongue.load()
    assert caught.value is failure
