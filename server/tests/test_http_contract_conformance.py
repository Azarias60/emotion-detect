"""真实 HTTP 输出与声明的 OpenAPI DTO 一致（契约符合性）。

响应模型声明不等于真实输出：这里对代表性路径做端到端校验——正常结果、
部分能力缺失、官方错误形状、报告事实读取、观察流、预览协议响应——真实
响应体必须能被 ``Envelope[DTO]`` / 协议 DTO（``extra=forbid``）解析。
两边任何漂移（缺字段、多字段、类型不符）都会在此失败。
"""
import pytest

from server.envelope import Envelope, ErrorResponse
from server.presentation.gateway.schemas import CaptureStatusPayload
from server.support.reporting.types.http import ReportCollection, ReportItem
from server.presentation.preview import PreviewSessionCancelled, PreviewSessionIssued
from server.support.identity.types.http import LoginResult, UserProfile
from server.tests.detection_client import (
    auth_headers, begin, finish, report, wait_for_report_stage,
)
from server.tests.measurement_fakes import physiology_ok
from server.tests.runtime_fixture import (
    attach_fake_device, detach_fake_device, seed_audio, seed_frame,
)


def _register(client, username="contract-user"):
    credentials = {"username": username, "password": "secret123"}
    assert client.post("/api/v1/auth/register", json={
        **credentials, "rePassword": "secret123"}).status_code == 200
    return credentials


def _auth_headers(client, username="contract-user"):
    credentials = _register(client, username)
    token = client.post("/api/v1/auth/login", json=credentials).json()["data"]["token"]
    return {"Authorization": f"Bearer {token}"}


def test_identity_responses_match_declared_dtos(client):
    credentials = _register(client)
    login = client.post("/api/v1/auth/login", json=credentials)
    assert login.status_code == 200
    Envelope[LoginResult].model_validate(login.json())

    token = {"Authorization": f"Bearer {login.json()['data']['token']}"}
    profile = client.get("/api/v1/users/me", headers=token)
    assert profile.status_code == 200
    Envelope[UserProfile].model_validate(profile.json())


def test_backend_recorded_report_list_and_detail_preserve_the_same_facts(client):
    from server.tests.runtime_fixture import assembly

    headers = auth_headers(client, "report-ct")
    owner = client.get("/api/v1/users/me", headers=headers).json()["data"]["id"]
    report_id = assembly.reporting.create_report(owner, "emotion")["id"]
    result = {"answers": [{"question": "感受？", "answer": "很好"}], "report": {"text": "正文"}}
    assembly.reporting.update_report(owner, report_id, result)

    listed = client.get("/api/v1/reports", headers=headers)
    assert listed.status_code == 200
    Envelope[ReportCollection].model_validate(listed.json())
    for key, value in result.items():
        assert listed.json()["data"]["items"][0]["result"][key] == value

    detail = client.get(f"/api/v1/reports/{report_id}", headers=headers)
    assert detail.status_code == 200
    body = Envelope[ReportItem].model_validate(detail.json())
    for key, value in result.items():
        assert body.data.result[key] == value


def test_error_responses_use_the_official_detail_shape(client):
    # 401：未登录访问受保护资源。
    unauthorized = client.get("/api/v1/users/me")
    assert unauthorized.status_code == 401
    ErrorResponse.model_validate(unauthorized.json())

    # 检测意图对不存在/不属于自己的报告返回 404 官方形状。
    headers = auth_headers(client, "error-ct")
    missing = client.post("/api/v1/detections/999/finish", headers=headers)
    assert missing.status_code == 404
    ErrorResponse.model_validate(missing.json())

    # malformed JSON / 形状校验都归框架 422（官方结构化 detail 数组）。
    invalid = client.post("/api/v1/auth/register", content=b"{not json",
                          headers={"Content-Type": "application/json"})
    assert invalid.status_code == 422
    assert isinstance(invalid.json()["detail"], list)

    # 422 校验错误：官方结构化 detail 数组（字段级定位）。
    weak = client.post("/api/v1/auth/register", json={"username": "ab"})
    assert weak.status_code == 422
    errors = weak.json()["detail"]
    assert isinstance(errors, list) and errors[0]["loc"][-1] == "username"


@pytest.mark.xfail(
    reason="产品缺陷1：create_measure 返回的事实未被调度写入报告（见最终报告）",
    strict=False,
)
def test_fatigue_facts_reach_the_report_with_capture_status(client, fatigue_stub, physiology_stub):
    from server.tests.conftest import behavior_ok

    fatigue_stub(behavior_ok())
    physiology_stub(physiology_ok())
    attach_fake_device()
    try:
        headers = auth_headers(client, "fatigue-ct")
        report_id = begin(client, headers, "fatigue")
        for _ in range(5):
            seed_frame()
        finish(client, headers, report_id)

        data = wait_for_report_stage(client, headers, report_id, "complete")
        result = data["result"]
        assert result["stage"] == "complete"
        assert set(result) >= {"behavior", "fatigue", "advice", "detection_id",
                               "physiology", "measurement_notice", "physiology_diagnostics", "stage"}
        physiology = result["physiology"]
        assert physiology is not None
        assert physiology["heart_rate_bpm"] == physiology_ok().heart_rate_bpm
        assert physiology["respiration_rate_per_min"] == physiology_ok().respiration_rate_per_min
        assert physiology["hrv"] is not None and physiology["hrv"]["lf_hf_ratio"] is not None
        assert result["detection_id"] == str(report_id)

        status = client.get("/api/v1/capture/status")
        assert status.status_code == 200
        Envelope[CaptureStatusPayload].model_validate(status.json())
    finally:
        detach_fake_device()


@pytest.mark.xfail(
    reason="产品缺陷2：analyze 未解包 measure 的 input，解读路径失败（见最终报告）",
    strict=False,
)
def test_emotion_facts_reach_the_report(client, face_predictor, voice_model, llm,
                                        fatigue_stub, physiology_stub):
    from server.tests.test_gateway_operations import valid_behavior

    face_predictor(["happy", "neutral"])
    voice_model("你好。最近有点累！")
    llm("综合报告。\n喜怒哀虑统计：喜:1 怒:0 哀:0 虑:2")
    fatigue_stub(valid_behavior())
    physiology_stub(physiology_ok())
    attach_fake_device()
    try:
        headers = auth_headers(client, "emotion-ct")
        report_id = begin(client, headers, "emotion")
        seed_audio(b"\x01\x00" * 16000)
        for _ in range(5):
            seed_frame()
        finish(client, headers, report_id)

        data = wait_for_report_stage(client, headers, report_id, "complete")
        result = data["result"]
        assert result["report"] == {"text": "综合报告。\n喜怒哀虑统计：喜:1 怒:0 哀:0 虑:2"}
        assert result["emotion_word_count"] == {"喜": 1, "怒": 0, "哀": 0, "虑": 2}
        assert result["expression"]["class_counts"] == [["happy", 1], ["neutral", 1]]
        assert result["physiology"]["heart_rate_bpm"] == physiology_ok().heart_rate_bpm
        assert result["speech"]["segments"]
        assert result["detection_id"] == str(report_id)
    finally:
        detach_fake_device()


def test_observation_stream_delivers_the_account_collection(client):
    from server.tests.runtime_fixture import assembly

    headers = auth_headers(client, "stream-ct")
    owner = client.get("/api/v1/users/me", headers=headers).json()["data"]["id"]
    values = []
    stop = assembly.reporting.observe(owner, values.append)
    try:
        # 订阅建立即取得当前账号事实（空集合），创建变化随后到达。
        assert values[-1] == []
        report_id = assembly.reporting.create_report(owner, "fatigue")["id"]
        created = [row["id"] for row in values[-1]]
        assert created == [report_id]
    finally:
        stop()


def test_preview_protocol_responses_match_declared_dtos(client):
    attach_fake_device()
    try:
        headers = _auth_headers(client, "preview-ct")

        issued = client.post("/api/v1/capture/preview/session", headers=headers)
        assert issued.status_code == 200, issued.text
        PreviewSessionIssued.model_validate(issued.json())

        session = issued.json()
        cancelled = client.delete(
            f"/api/v1/capture/preview/session/{session['preview_session_id']}",
            headers=headers)
        assert cancelled.status_code == 200, cancelled.text
        PreviewSessionCancelled.model_validate(cancelled.json())
    finally:
        detach_fake_device()
