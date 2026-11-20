"""HTTP semantics and failure propagation: processing failures land in report facts.

检测执行链迁到业务能力后，错误不再有独立的 HTTP 失败路径：处理环节的
typed 失败按公开文案写入报告 ``failure`` 事实，未分类失败收敛为固定公
开文案。这里覆盖观测路由的 400 语义、报告事实中的失败与指标投影，以及
测量准入在取消时的占用语义。
"""

import asyncio
import pytest
from threading import Event
from types import SimpleNamespace

from server.app import create_app
from server.concurrency import TaskAdmission
from server.detection.flows import create_measure
from server.detection_errors import (
    DetectionBusyError,
    DetectionFlowError,
    DetectionTimeoutError,
    DetectionUnavailableError,
    DetectionUpstreamError,
)
from server.tests.conftest import behavior_ok
from server.tests.detection_client import (
    auth_headers, begin, finish, wait_for_report_stage,
)
from server.tests.detection_steps import start_detection
from server.tests.measurement_fakes import physiology_ok as physiology_measurement
from server.tests.runtime_fixture import attach_fake_device
from server.tests.test_gateway_operations import (
    FakeBehavior,
    FakeCapture,
    FakeFatigueAdvice,
    FakeFatigueScoring,
    FakeWindow,
    valid_behavior,
)

OBSERVATION_ROUTES = (
    "observations/expression", "observations/behavior", "observations/speech",
)

# typed 检测错误按自身公开文案进入报告；未分类失败固定收敛公开文案。
MEASUREMENT_FAILURES = (
    DetectionUnavailableError("测量能力不可用"),
    DetectionUpstreamError("测量上游失败"),
    DetectionTimeoutError("测量超时"),
    DetectionFlowError("secret flow detail"),
    RuntimeError("measurement inference defect"),
)
UNCLASSIFIED_FAILURE = "检测失败，请查看服务端日志"


@pytest.mark.parametrize("path", OBSERVATION_ROUTES)
def test_missing_required_analysis_input_is_a_business_error(client, path):
    """无 token 的观测路由：缺输入是业务语义（400 + 官方 detail）。"""
    response = client.post("/api/v1/" + path)
    assert response.status_code == 400
    assert isinstance(response.json()["detail"], str)


def test_unexpected_http_boundary_failure_uses_the_framework_default():
    """未捕获异常走官方默认 500（纯文本），实现细节不外泄。"""
    app = create_app()

    @app.get("/test-internal-error")
    def broken():
        raise TypeError("internal-secret-defect")

    from fastapi.testclient import TestClient

    response = TestClient(app, raise_server_exceptions=False).get("/test-internal-error")
    assert response.status_code == 500
    assert response.text == "Internal Server Error"
    assert "internal-secret" not in response.text


def test_authentication_challenge_header_survives_error_envelope(client):
    response = client.get("/api/v1/users/me")
    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"


def test_json_syntax_and_field_constraints_have_distinct_http_statuses(client):
    # malformed JSON 与形状校验都归框架 422（结构化 detail 数组）。
    malformed = client.post(
        "/api/v1/detections", content="{", headers={"Content-Type": "application/json"})
    assert malformed.status_code == 422
    assert isinstance(malformed.json()["detail"], list)
    headers = auth_headers(client, "shape-user")
    missing_body = client.post("/api/v1/detections", headers=headers)
    assert missing_body.status_code == 422  # 框架校验（缺 body）
    # 意图请求体只声明 kind：历史键不是声明字段，提交即框架 422。
    stray = client.post(
        "/api/v1/detections", json={"kind": "health", "preparation": {"answers": [{"answer": 1}]}},
        headers=headers)
    assert stray.status_code == 422
    assert isinstance(stray.json()["detail"], list)
    # 传感器来源字段已删除：多余表单字段被忽略，行为观察按数据缺失返回 400。
    response = client.post("/api/v1/observations/behavior")
    assert response.status_code == 400


# --- 处理失败进入报告事实 ------------------------------------------------------


@pytest.mark.xfail(
    reason="产品缺陷2：analyze 未解包 measure 的 input，解读入口即失败（见最终报告）", strict=False,
)
@pytest.mark.parametrize("kind", ["emotion", "health"])
def test_empty_llm_reply_records_upstream_failure_in_the_report(
    client, request, kind, fatigue_stub, physiology_stub, llm,
):
    """空 LLM 回复使 analyze 抛 DetectionUpstreamError：报告 stage=failed，
    failure 是该能力的公开文案，无部分成功伪装。"""
    llm("")
    headers, report_id = start_detection(client, request, kind, f"empty-{kind}")
    fatigue_stub(behavior_ok())
    physiology_stub(physiology_measurement())
    finish(client, headers, report_id)

    expected = ("情绪报告服务返回空内容" if kind == "emotion"
                else "健康报告服务返回空内容")
    data = wait_for_report_stage(client, headers, report_id, "failed")
    assert data["result"]["failure"] == expected
    assert "report" not in data["result"]


@pytest.mark.parametrize("kind", ["fatigue", "emotion", "health"])
@pytest.mark.parametrize("failure", MEASUREMENT_FAILURES, ids=lambda e: type(e).__name__)
def test_measurement_reader_failures_reach_the_report_without_substitute(
    client, request, kind, failure, fatigue_stub, physiology_stub, llm,
):
    """测量读取器各类失败经 create_measure 后写入报告：typed 错误保留公开
    文案，未分类失败收敛为固定文案；解读端口不被触碰。"""
    llm("不应被调用的报告")
    headers, report_id = start_detection(client, request, kind, f"measure-{kind}")
    fatigue_stub(behavior_ok())
    physiology_stub(error=failure)
    finish(client, headers, report_id)

    data = wait_for_report_stage(client, headers, report_id, "failed")
    result = data["result"]
    assert result["failure"] == (
        UNCLASSIFIED_FAILURE if type(failure) in (RuntimeError, DetectionFlowError)
        else str(failure)
    )
    assert "secret flow detail" not in result["failure"]
    assert "measurement inference defect" not in result["failure"]


@pytest.mark.xfail(
    reason="产品缺陷1+2：measure 事实未写入报告且 analyze 未解包 input（见最终报告）", strict=False,
)
@pytest.mark.parametrize("kind", ["fatigue", "emotion", "health"])
def test_available_physiology_reaches_the_report_facts(
    client, request, kind, fatigue_stub, physiology_stub, llm,
):
    llm("综合报告正文。")
    headers, report_id = start_detection(client, request, kind, f"physio-{kind}")
    fatigue_stub(behavior_ok())
    physiology_stub(physiology_measurement())
    finish(client, headers, report_id)

    data = wait_for_report_stage(client, headers, report_id, "complete")
    result = data["result"]
    physiology = result["physiology"]
    assert physiology["heart_rate_bpm"] == physiology_measurement().heart_rate_bpm
    assert physiology["hrv"]["lf_hf_ratio"] == 1.7
    assert result["detection_id"] == str(report_id)


# --- 测量准入的使用期 ---------------------------------------------------------


def test_cancellation_holds_admission_until_measurement_worker_exits():
    started, release = Event(), Event()

    class BlockingReader:
        def result(self, source):
            started.set()
            assert release.wait(3)
            return None

    admission = TaskAdmission()
    reports = SimpleNamespace(report_facts=lambda report_id: {"type": "fatigue", "result": {}})
    from server.analysis.fatigue.reporting.flow import FatigueAnalysisFlowService

    measure = create_measure(
        reports=reports, admission=admission,
        fatigue_flow=FatigueAnalysisFlowService(
            behavior=FakeBehavior(valid_behavior()),
            fatigue_scoring=FakeFatigueScoring(),
            fatigue_advice=FakeFatigueAdvice(),
            facephys_factory=BlockingReader,
        ),
        emotion_flow=None, health_flow=None,
    )
    media = FakeCapture(window=FakeWindow())

    async def scenario():
        task = asyncio.create_task(measure("report-1", media, ()))
        try:
            assert await asyncio.to_thread(started.wait, 3)
            task.cancel()
            await asyncio.sleep(0)
            assert not task.done()
            with pytest.raises(DetectionBusyError), admission.acquire():
                pass
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)

        assert task.cancelled()
        with admission.acquire():
            pass

    asyncio.run(scenario())
    assert media.window_calls == 1
