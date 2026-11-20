# -*- coding: utf-8 -*-
"""新树测试公共夹具。

契约测试不加载任何权重、不访问网络、不连接串口：TestClient 不进入
lifespan（with 之外使用），模型槽位由各用例显式注入替身。
"""
import os
from types import SimpleNamespace
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from server.tests import contract_stubs as stubs  # noqa: E402
from server.tests.runtime_fixture import (  # noqa: E402
    assembly as _TEST_ASSEMBLY,
    expression as expression_service,
    kernel,
    speech as speech_service,
    tongue as tongue_service,
)
from server.analysis import llm_client as llm_service  # noqa: E402
from server.compute.fatigue.behavior_recognition.domain import summarize_behavior  # noqa: E402
from server.tests.lease_view import LeaseView  # noqa: E402
from server import settings  # noqa: E402
from server import app as application  # noqa: E402


# 域服务在装配时捕获内核与模型槽位，共享测试装配必须先于路由构建就位。
test_app = application.create_app(_TEST_ASSEMBLY)


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "real_behavior_adapter: 直接使用真实 VideoBehaviorRecognizer，绕过基线替身",
    )
    config.addinivalue_line(
        "markers",
        "model: 需要完整模型或第三方模型框架，仅在模型集成 CI 中执行",
    )


def behavior_ok():
    """A valid typed behavior observation for tests that do not depend on scoring levels."""
    return summarize_behavior(
        blink_count=10, yawn_count=0, nod_count=0,
        closed_seconds=0.0, speaking_seconds=0.0, face_seconds=60.0, video_seconds=60.0,
    )



@pytest.fixture()
def api():
    """被测应用外观：三个分析锁（与旧树 api 夹具同形）。"""
    import types

    ws = _TEST_ASSEMBLY.admission
    return types.SimpleNamespace(
        emotion_analysis_lock=LeaseView(ws),
        fatigue_analysis_lock=LeaseView(ws),
        health_analysis_lock=LeaseView(ws),
    )


@pytest.fixture()
def client():
    """整个用例共用一个事件循环（不触发 lifespan）：检测调度的问答循环、
    采集时钟与处理任务都是请求间持续运行的后台任务，必须跨请求存活。
    模型槽位由用例显式注入替身。``raise_server_exceptions=False`` 与生产
    一致：未预期异常由应用级 500 处理器生成响应后，服务器侧照常记录
    重抛的堆栈，客户端只见公共封套。"""
    import anyio

    test_client = TestClient(test_app, raise_server_exceptions=False)
    with anyio.from_thread.start_blocking_portal(**test_client.async_backend) as portal:
        test_client.portal = portal
        yield test_client


@pytest.fixture(autouse=True)
def isolated_database(tmp_path, monkeypatch):
    """每个用例使用独立身份/报告库：注册、登录与报告事实互不串扰。

    检测执行、路由与观察传输都持有 ReportingService/IdentityDependencies
    本体，这里只替换它们内部的存储与身份服务，装配对象保持不变。
    """
    from server.support.identity.adapters import sqlite as identity_database
    from server.support.identity.assembly import create_identity_dependencies
    from server.support.reporting.adapters import sqlite as reporting_database
    from server.support.reporting.adapters.sqlite import SqliteReportRepository

    database = tmp_path / "isolated.db"
    identity_database.init_database(database)
    reporting_database.init_database(database)
    _TEST_ASSEMBLY.reporting.reports = SqliteReportRepository(database)
    monkeypatch.setattr(
        _TEST_ASSEMBLY.identity, "identity", create_identity_dependencies(database).identity)


@pytest.fixture(autouse=True)
def baseline_state(monkeypatch, request):
    """每个用例从同一基线状态开始：模型缺失、LLM 替身、无设备与缓存数据。"""
    expression_service.close()
    speech_service.close()
    tongue_service.close()
    _TEST_ASSEMBLY.behavior.close()
    _TEST_ASSEMBLY.facephys.close()
    if not request.node.get_closest_marker("real_behavior_adapter"):
        monkeypatch.setattr(_TEST_ASSEMBLY.behavior, "_processor", SimpleNamespace(
            observe_frames=lambda frames: behavior_ok(), close=lambda: None,
        ))
    monkeypatch.setattr(llm_service, "OpenAI", stubs.FakeOpenAI)
    monkeypatch.setitem(settings.AI_CONFIG, "api_key", "")
    monkeypatch.setitem(settings.AI_CONFIG, "base_url", "http://127.0.0.1:8082/v1")
    monkeypatch.setitem(settings.AI_CONFIG, "model", "qwen3.8-flash-next-iq3_s")
    stubs.FakeOpenAI.instances.clear()
    from server.tests.runtime_fixture import reset_capture
    reset_capture()
    yield


@pytest.fixture()
def face_predictor(monkeypatch):
    def _install(emotions=("happy", "neutral")):
        stub = stubs.StubFacePredictor(emotions)
        monkeypatch.setattr(expression_service, "_predictor_factory", lambda: stub)
        expression_service.load()
        return stub

    return _install


@pytest.fixture()
def tongue_model(monkeypatch):
    def _install(result=None, error=None):
        stub = stubs.StubTonguePredictor()
        if result:
            for dimension, value in result.items():
                stub.result[dimension].update(value)
        stub.error_to_raise = error
        monkeypatch.setattr(tongue_service, "model", stub)
        return stub

    return _install


@pytest.fixture()
def voice_model(monkeypatch):
    def _install(raw_text):
        stub = stubs.StubVoiceModel(raw_text)
        monkeypatch.setattr(speech_service, "model", stub)
        return stub

    return _install


@pytest.fixture()
def fatigue_stub(monkeypatch):
    def _install(result=None, error=None):
        calls = []

        def observe_frames(frames):
            calls.append({"frames": tuple(frames)})
            if error is not None:
                raise error
            return result

        observe_frames.calls = calls
        monkeypatch.setattr(_TEST_ASSEMBLY.behavior, "_processor", SimpleNamespace(
            observe_frames=observe_frames, close=lambda: None,
        ))
        return observe_frames

    return _install


@pytest.fixture()
def physiology_stub(monkeypatch):
    """替身测量 pass：只替换服务内的一次视频 pass 执行口。"""

    def _install(result=None, error=None):
        stub = stubs.make_analysis_stub(result=result, error=error)
        from server.compute.facephys.service import FacePhysService

        monkeypatch.setattr(
            FacePhysService, "_run_pass",
            lambda self, source: stub(source, None),
        )
        return stub

    return _install


@pytest.fixture()
def llm(monkeypatch):
    """启用 LLM 替身并设定固定回复；调用参数记录在类级 instances。

    同时注入测试密钥，覆盖带凭据的调用方式。
    """

    def _install(reply):
        monkeypatch.setitem(settings.AI_CONFIG, "api_key", "test-key")
        monkeypatch.setattr(llm_service, "OpenAI", stubs.FakeOpenAI)
        monkeypatch.setattr(stubs.FakeOpenAI, "reply", reply)
        return stubs.FakeOpenAI

    return _install
