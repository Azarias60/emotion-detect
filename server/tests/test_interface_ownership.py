"""HTTP 适配边界：认证和浏览器可用的操作范围。"""
import pytest


def test_report_http_has_no_frontend_result_write_or_saved_summary_endpoint():
    from server.tests.conftest import test_app

    paths = test_app.openapi()["paths"]
    assert "post" not in paths.get("/api/v1/reports", {}), "浏览器不再自供检测结果副本"
    assert "/api/v1/reports/analyses" not in paths, "临时汇总不创建后端保存任务"



@pytest.mark.parametrize("kind", ["health", "fatigue", "emotion"])
def test_detection_start_uses_the_authenticated_workspace(client, kind):
    response = client.post("/api/v1/detections", json={"kind": kind})
    assert response.status_code == 401, "检测启动与报告归属使用同一认证身份"
