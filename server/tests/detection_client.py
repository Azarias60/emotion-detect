"""按目标流程驱动检测：登录后提交模式意图，封存后经报告读取事实。

启动只提交模式与身份；准备、采集、问答与后续处理由后端安排。检测
事实通过报告观察/详情读取，本助手在测试内轮询阶段事实以等待后台
处理完成。
"""
import time


def auth_headers(client, username="detection-user", password="secret123"):
    credentials = {"username": username, "password": password}
    registered = client.post("/api/v1/auth/register", json={**credentials, "rePassword": password})
    assert registered.status_code == 200, registered.text
    token = client.post("/api/v1/auth/login", json=credentials).json()["data"]["token"]
    return {"Authorization": f"Bearer {token}"}


def begin(client, headers, kind):
    """启动检测：返回本轮报告标识。"""
    response = client.post("/api/v1/detections", json={"kind": kind}, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()["data"]["reportId"]


def finish(client, headers, report_id):
    """提前结束采集并封存；后续处理在后台继续。"""
    response = client.post(f"/api/v1/detections/{report_id}/finish", headers=headers)
    assert response.status_code == 200, response.text


def cancel(client, headers, report_id):
    response = client.post(f"/api/v1/detections/{report_id}/cancel", headers=headers)
    assert response.status_code == 200, response.text


def report(client, headers, report_id):
    response = client.get(f"/api/v1/reports/{report_id}", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()["data"]


def wait_for_report_stage(client, headers, report_id, stage, timeout=10.0):
    """轮询报告阶段事实，等待后台处理到达指定阶段。"""
    deadline = time.monotonic() + timeout
    data = report(client, headers, report_id)
    while time.monotonic() < deadline:
        if data["result"].get("stage") == stage:
            return data
        time.sleep(0.05)
        data = report(client, headers, report_id)
    raise AssertionError(f"报告未在时限内到达阶段 {stage!r}：{data['result']!r}")


def wait_report_gone(client, headers, report_id, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = client.get(f"/api/v1/reports/{report_id}", headers=headers)
        if response.status_code == 404:
            return
        time.sleep(0.05)
    raise AssertionError("报告未在时限内被移除")
