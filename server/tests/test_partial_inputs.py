"""部分能力缺失与实现损坏的边界：处理失败记录为报告事实，无降级路径。"""

import pytest

from server.tests.detection_client import (
    auth_headers, begin, finish, report, wait_for_report_stage,
)
from server.tests.measurement_fakes import physiology_ok
from server.tests.runtime_fixture import attach_fake_device, detach_fake_device, seed_audio, seed_frame


def test_broken_fatigue_implementation_records_failure_without_substitute(
    client, monkeypatch,
):
    # 行为模型损坏时注入 typed 之外的运行时错误，验证疲劳处理没有降级
    # 路径：处理失败进入报告事实（固定公开文案），不转成缺测或替代结果。
    from server.tests.runtime_fixture import assembly
    monkeypatch.setattr(assembly.behavior._processor, "observe_frames",
                        lambda frames: (_ for _ in ()).throw(RuntimeError("damaged landmark weights")))
    attach_fake_device()
    headers = auth_headers(client, "fatigue-broken")
    report_id = begin(client, headers, "fatigue")
    seed_frame()
    finish(client, headers, report_id)

    data = wait_for_report_stage(client, headers, report_id, "failed")
    assert data["result"]["failure"] == "检测失败，请查看服务端日志"
    assert "damaged landmark weights" not in repr(data["result"])


def test_voice_implementation_failure_records_failure_not_degradation(
    client, face_predictor, fatigue_stub, physiology_stub, llm, voice_model, monkeypatch,
):
    face_predictor(["happy"])
    fatigue_stub()
    physiology_stub(physiology_ok())
    voice_model("设备录音")
    llm("视频分析完成")

    def fail(*args, **kwargs):
        raise RuntimeError("no audio stream")

    from server.compute.speech.service import SpeechService
    monkeypatch.setattr(SpeechService, "result", fail)
    attach_fake_device()
    headers = auth_headers(client, "voice-broken")
    report_id = begin(client, headers, "emotion")
    seed_audio(b"\x01\x00" * 16000)
    finish(client, headers, report_id)

    data = wait_for_report_stage(client, headers, report_id, "failed")
    assert data["result"]["failure"] == "检测失败，请查看服务端日志"


@pytest.mark.parametrize("kind", ["health", "fatigue", "emotion"])
def test_intent_rejects_undeclared_request_fields(client, kind):
    """意图请求体只接受 kind；多余字段由框架 422 拒绝。"""
    headers = auth_headers(client, f"shape-{kind}")
    response = client.post("/api/v1/detections", json={
        "kind": kind, "preparation": {"questions": [1]},
    }, headers=headers)
    assert response.status_code == 422
    assert isinstance(response.json()["detail"], list)


def test_all_missing_detection_records_failure_with_input_guidance(client, llm):
    """启动成功但无任何可用证据：处理失败如实记录，不产生替代结果。"""
    llm("不应被调用")
    attach_fake_device()
    try:
        headers = auth_headers(client, "all-missing")
        report_id = begin(client, headers, "emotion")
        finish(client, headers, report_id)

        data = wait_for_report_stage(client, headers, report_id, "failed")
        assert "全部检测数据缺失" in data["result"]["failure"]
    finally:
        detach_fake_device()
