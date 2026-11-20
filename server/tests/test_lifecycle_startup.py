import asyncio
from types import SimpleNamespace

import pytest

import server.app as application
from server.bootstrap import assemble
from server.detection_errors import DetectionUnavailableError
from server.tests.runtime_fixture import assembly

MODEL_LOADERS = ["expression", "speech", "tongue", "behavior", "facephys"]


def _patch_loader(monkeypatch, target, name, loader):
    getattr(target, name).close()
    factory = {"expression": "_predictor_factory", "speech": "_model_factory",
               "tongue": "_predictor_factory", "behavior": "_processor_factory",
               "facephys": "_reader_factory"}[name]
    monkeypatch.setattr(getattr(target, name), factory, loader)


@pytest.mark.parametrize("stage", MODEL_LOADERS + ["init_identity_database", "init_reporting_database"])
@pytest.mark.parametrize("error_type", [TypeError, RuntimeError])
def test_startup_programming_or_database_failure_propagates_and_closes_capture(monkeypatch, stage, error_type):
    events = []
    app = application.create_app(assembly)
    async def close():
        events.append("closed")
    failure = error_type("startup defect")
    def fail(*args):
        raise failure
    monkeypatch.setattr(assembly.kernel, "close", close)
    for loader in MODEL_LOADERS:
        _patch_loader(monkeypatch, assembly, loader, fail if stage == loader else lambda *args: SimpleNamespace(is_available=True, close=lambda: None))
    for initializer in ["init_identity_database", "init_reporting_database"]:
        monkeypatch.setattr(assembly, initializer, fail if stage == initializer else lambda: None)
    async def scenario():
        with pytest.raises(error_type, match="startup defect") as caught:
            async with app.router.lifespan_context(app):
                pytest.fail("failed startup must not serve requests")
        assert caught.value is failure
    asyncio.run(scenario())
    assert events == ["closed"]


@pytest.mark.parametrize("loader", MODEL_LOADERS)
@pytest.mark.parametrize("error_type", [DetectionUnavailableError, ModuleNotFoundError, FileNotFoundError])
def test_required_model_failure_aborts_startup_and_cleans_up(monkeypatch, loader, error_type):
    app = application.create_app(assembly)
    events = []
    available = SimpleNamespace(close=lambda: None)
    failure = error_type("model dependency missing")

    def fail():
        raise failure

    async def close():
        events.append("closed")

    monkeypatch.setattr(assembly.kernel, "close", close)
    for name in MODEL_LOADERS:
        _patch_loader(monkeypatch, assembly, name, fail if name == loader else lambda: available)
    monkeypatch.setattr(assembly, "init_identity_database", lambda: events.append("identity"))
    monkeypatch.setattr(assembly, "init_reporting_database", lambda: events.append("reporting"))

    async def scenario():
        with pytest.raises(error_type) as caught:
            async with app.router.lifespan_context(app):
                pytest.fail("failed startup must not serve requests")
        assert caught.value is failure

    asyncio.run(scenario())
    assert events == ["closed"]


def test_unknown_startup_failure_closes_real_capture_session_and_audio_track(monkeypatch):
    from aiortc import AudioStreamTrack, RTCPeerConnection, RTCConfiguration
    from server.capture.webrtc.kernel import CaptureKernel

    def fail():
        raise RuntimeError("startup defect")

    async def scenario():
        kernel = CaptureKernel()
        target = assemble(kernel=kernel)
        app = application.create_app(target)
        monkeypatch.setattr(target.expression, "load", fail)
        peer = RTCPeerConnection(RTCConfiguration(iceServers=[]))
        track = AudioStreamTrack()
        try:
            session = await kernel.hub.publish(peer, track)
            with pytest.raises(RuntimeError, match="startup defect"):
                async with app.router.lifespan_context(app):
                    pytest.fail("failed startup must not serve requests")
            assert kernel.hub.current is None
            assert session.closed and session.jpeg.closed
            assert session.audio._monitor.done()
            assert peer.connectionState == "closed"
            assert track.readyState == "ended"
        finally:
            await kernel.close()
            await peer.close()
            track.stop()
    asyncio.run(scenario())


def test_lifespan_exception_always_closes_capture_kernel(monkeypatch):
    events = []
    for loader in MODEL_LOADERS:
        _patch_loader(monkeypatch, assembly, loader, lambda *args: SimpleNamespace(is_available=True, close=lambda: None))
    monkeypatch.setattr(assembly, "init_identity_database", lambda: None)
    monkeypatch.setattr(assembly, "init_reporting_database", lambda: None)
    kernel = assembly.kernel

    async def close_kernel():
        events.append("close")

    monkeypatch.setattr(kernel, "close", close_kernel)

    async def scenario():
        app = application.create_app(assembly)
        with pytest.raises(ValueError, match="shutdown failed"):
            async with app.router.lifespan_context(app):
                raise ValueError("shutdown failed")

    asyncio.run(scenario())
    assert events == ["close"]


def test_create_app_repeatability_and_isolation():
    """验证 create_app 可被重复调用，每次返回独立应用与独立路由，互不干扰。"""
    app1 = application.create_app()
    app2 = application.create_app()
    assert app1 is not app2
    assert app1.router is not app2.router


def test_lifespan_symmetric_shutdown_closes_all_models_and_services(monkeypatch):
    """验证生命周期正常退出时，expression、speech、tongue、preview_manager 和 kernel 均被对称关闭。"""
    events = []

    test_assembly = assemble()

    monkeypatch.setattr(test_assembly.expression, "close", lambda: events.append("expression_close"))
    monkeypatch.setattr(test_assembly.speech, "close", lambda: events.append("speech_close"))
    monkeypatch.setattr(test_assembly.tongue, "close", lambda: events.append("tongue_close"))

    original_kernel_close = test_assembly.kernel.close
    async def spy_kernel_close():
        events.append("kernel_close")
        if original_kernel_close is not None:
            await original_kernel_close()
    monkeypatch.setattr(test_assembly.kernel, "close", spy_kernel_close)

    monkeypatch.setattr(test_assembly.expression, "load", lambda: events.append("expression_load"))
    monkeypatch.setattr(test_assembly.speech, "load", lambda: events.append("speech_load"))
    monkeypatch.setattr(test_assembly.tongue, "load", lambda: events.append("tongue_load"))
    monkeypatch.setattr(test_assembly, "init_identity_database", lambda: None)
    monkeypatch.setattr(test_assembly, "init_reporting_database", lambda: None)

    for name in ("behavior", "facephys"):
        monkeypatch.setattr(getattr(test_assembly, name), "load", lambda: None)

    app = application.create_app(test_assembly)

    async def scenario():
        async with app.router.lifespan_context(app):
            assert "expression_load" in events
            assert "speech_load" in events
            assert "tongue_load" in events
            assert "expression_close" not in events
            assert "speech_close" not in events
            assert "tongue_close" not in events

    asyncio.run(scenario())

    assert "expression_close" in events
    assert "speech_close" in events
    assert "tongue_close" in events
    assert "kernel_close" in events


def test_lifespan_shutdown_fault_tolerance_continues_cleanup(monkeypatch):
    """验证当某个模型 close() 抛出异常时，其余模型、预览管理器与采集内核仍被全部执行关闭。"""
    events = []
    test_assembly = assemble()

    def faulty_expression_close():
        events.append("expression_fail")
        raise RuntimeError("expression close failure")

    monkeypatch.setattr(test_assembly.expression, "close", faulty_expression_close)
    monkeypatch.setattr(test_assembly.speech, "close", lambda: events.append("speech_close"))
    monkeypatch.setattr(test_assembly.tongue, "close", lambda: events.append("tongue_close"))

    async def spy_kernel_close():
        events.append("kernel_close")
    monkeypatch.setattr(test_assembly.kernel, "close", spy_kernel_close)

    monkeypatch.setattr(test_assembly.expression, "load", lambda: None)
    monkeypatch.setattr(test_assembly.speech, "load", lambda: None)
    monkeypatch.setattr(test_assembly.tongue, "load", lambda: None)
    monkeypatch.setattr(test_assembly, "init_identity_database", lambda: None)
    monkeypatch.setattr(test_assembly, "init_reporting_database", lambda: None)

    for name in ("behavior", "facephys"):
        monkeypatch.setattr(getattr(test_assembly, name), "load", lambda: None)

    app = application.create_app(test_assembly)

    async def scenario():
        async with app.router.lifespan_context(app):
            pass

    asyncio.run(scenario())

    assert "expression_fail" in events
    assert "speech_close" in events
    assert "tongue_close" in events
    assert "kernel_close" in events


def test_server_app_module_has_no_global_app_instance():
    """验证 server.app 模块不再暴露全局 app 单例，消除模块导入期隐式装配。"""
    import server.app as app_mod
    assert not hasattr(app_mod, "app")
    assert callable(getattr(app_mod, "create_app", None))


def test_real_database_initializers_run_from_the_supplied_assembly(monkeypatch, tmp_path):
    """Keep the real lifespan/database path; replace only heavy model factories.

    装配根用同一个 database_path 驱动身份库与报告库初始化（报告表对用户
    表有外键，两侧必须同库同源）；报告模块不再有全局路径接缝可绕。
    """
    import sqlite3
    from server import settings

    database_path = tmp_path / "application.db"
    monkeypatch.setattr(settings, "UPLOAD_DIR", tmp_path / "uploads")
    target = assemble(database_path=database_path)
    for name in MODEL_LOADERS:
        _patch_loader(monkeypatch, target, name, lambda: SimpleNamespace(close=lambda: None))
    app = application.create_app(target)

    async def scenario():
        async with app.router.lifespan_context(app):
            with sqlite3.connect(database_path) as connection:
                tables = {row[0] for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'",
                )}
            assert {"users", "reports"} <= tables
            # 复现原阻断项：两库必须同路径，报告的账号外键才能解析。
            registered = target.identity.identity.register("reporter", "secret123")
            created = target.reporting.create_report(registered["id"], "emotion")
            assert set(created) == {"id"}
            events = []
            stop = target.reporting.observe(registered["id"], events.append)
            try:
                target.reporting.update_report(registered["id"], created["id"], {
                    "stage": "complete", "report": {"text": "正文"},
                })
                assert events and events[-1][0]["id"] == created["id"]
                assert events[-1][0]["result"]["stage"] == "complete"
            finally:
                stop()
            target.reporting.delete_report(registered["id"], created["id"])
            assert target.reporting.list_reports(registered["id"]) == {"items": []}

    asyncio.run(scenario())


@pytest.mark.parametrize("name", MODEL_LOADERS)
def test_resident_models_are_lazy_and_isolated_between_assemblies(monkeypatch, name):
    first, second = assemble(), assemble()
    loaded, closed = [], []

    def factory():
        model = SimpleNamespace(close=lambda: closed.append(model))
        loaded.append(model)
        return model

    for target in (first, second):
        _patch_loader(monkeypatch, target, name, factory)
    assert loaded == []
    left, right = getattr(first, name), getattr(second, name)
    left.load()
    right.load()
    assert len(loaded) == 2 and loaded[0] is not loaded[1]
    left.close()
    # The fatigue processor owns a native close(); other slots are released.
    if name == "behavior":
        assert closed == [loaded[0]]
    right.close()
    if name == "behavior":
        assert closed == loaded
