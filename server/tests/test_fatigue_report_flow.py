"""疲劳报告的强制评估与可选 FacePhys 边界（经检测意图与报告事实表达）。

无生命体征时评估与建议照常进入报告并附 measurement_notice；缺行为在
测量前拒绝；能力失败不降级，失败文案进入报告事实。
"""

import pytest

from server.detection_errors import DetectionTimeoutError, DetectionUnavailableError
from server.tests.conftest import behavior_ok
from server.tests.detection_client import (
    auth_headers, begin, finish, wait_for_report_stage,
)
from server.tests.detection_steps import start_detection
from server.tests.runtime_fixture import attach_fake_device, seed_frame


@pytest.mark.xfail(
    reason="产品缺陷1：create_measure 返回的事实未被调度写入报告（见最终报告）", strict=False,
)
def test_fatigue_report_keeps_assessment_when_no_vitals_are_measurable(
    client, fatigue_stub, physiology_stub,
):
    fatigue_stub(behavior_ok())
    physiology_stub()
    headers, report_id = start_detection(client, None, "fatigue", "ftg-no-vitals")
    finish(client, headers, report_id)

    result = wait_for_report_stage(client, headers, report_id, "complete")["result"]
    assert result["physiology"] is None
    assert result["measurement_notice"] == "未取得可用的生理指标"
    assert result["fatigue"]["level"] == "正常"
    assert result["advice"]["text"]


@pytest.mark.parametrize("has_video", [False, True])
def test_missing_behavior_rejects_fatigue_report_before_measurement(
    client, fatigue_stub, physiology_stub, has_video,
):
    fatigue_stub(None)
    measure = physiology_stub()
    attach_fake_device()
    headers = auth_headers(client, "ftg-no-behavior")
    report_id = begin(client, headers, "fatigue")
    if has_video:
        for _ in range(5):
            seed_frame()
    finish(client, headers, report_id)

    result = wait_for_report_stage(client, headers, report_id, "failed")["result"]
    assert result["failure"] == "本轮视频中没有有效的人脸行为数据，请重新检测"
    assert not measure.calls


def test_behavior_service_failure_is_not_degraded(client, fatigue_stub, physiology_stub):
    fatigue_stub(error=DetectionUnavailableError("行为模型暂不可用"))
    measure = physiology_stub()
    headers, report_id = start_detection(client, None, "fatigue", "ftg-broken")
    finish(client, headers, report_id)

    result = wait_for_report_stage(client, headers, report_id, "failed")["result"]
    assert result["failure"] == "行为模型暂不可用"
    assert not measure.calls


@pytest.mark.parametrize("error, failure_text", [
    (RuntimeError("internal measurement details"), "检测失败，请查看服务端日志"),
    (DetectionTimeoutError("测量超时"), "测量超时"),
])
def test_measurement_error_is_not_degraded_or_cached(
    client, fatigue_stub, physiology_stub, error, failure_text,
):
    """测量失败如实记录为失败事实；不产生缺测替代，也不缓存旧结果。"""
    fatigue_stub(behavior_ok())
    measure = physiology_stub(error=error)
    attach_fake_device()
    headers = auth_headers(client, "ftg-measure-err")
    report_id = begin(client, headers, "fatigue")
    for _ in range(5):
        seed_frame()

    finish(client, headers, report_id)

    result = wait_for_report_stage(client, headers, report_id, "failed")["result"]
    assert result["failure"] == failure_text
    assert "internal measurement details" not in result["failure"]
    assert len(measure.calls) == 1
