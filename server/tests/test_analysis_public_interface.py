"""Public composition contract: the app exposes Gateway routes and capability ports only."""

from server import bootstrap
from server.app import create_app
from server.bootstrap import assemble

GATEWAY_PATHS = {
    # 检测意图
    "/api/v1/detections",
    "/api/v1/detections/{report_id}/finish",
    "/api/v1/detections/{report_id}/cancel",
    "/api/v1/detections/{report_id}/retry",
    "/api/v1/detections/{report_id}/usage",
    "/api/v1/detections/{report_id}/questions/displayed",
    # 观测与采集状态
    "/api/v1/observations/expression",
    "/api/v1/observations/behavior",
    "/api/v1/observations/speech",
    "/api/v1/capture/status",
    # 报告事实
    "/api/v1/reports",
    "/api/v1/reports/{report_id}",
    "/api/v1/observations/reports",
}


def test_app_state_has_no_analysis_or_use_case_aggregate():
    app = create_app(assemble())

    assert not hasattr(bootstrap, "create_components")
    assert not hasattr(app.state, "use_cases")
    assert not hasattr(app.state, "analysis_services")
    assert not hasattr(app.state, "runtime")
    for runner in (
        "emotion_runner", "fatigue_runner", "health_runner",
        "tongue_runner", "captured_tongue_runner",
    ):
        assert not hasattr(app.state, runner), runner


def test_app_registers_only_the_gateway_operation_routes():
    app = create_app(assemble())

    paths = set(app.openapi()["paths"])

    assert GATEWAY_PATHS <= paths
    assert "/api/v1/hardware/video-stream" not in paths
