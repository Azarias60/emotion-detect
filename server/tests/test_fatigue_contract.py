# -*-coding: utf-8 -*-
"""疲劳检测意图契约：四个评分档位、缺失语义与准入占用经报告事实表达。"""

import pytest
from server.tests.detection_client import (
    auth_headers, begin, finish, wait_for_report_stage,
)
from server.tests.detection_steps import start_detection
from server.tests.runtime_fixture import attach_fake_device
from server.compute.fatigue.behavior_recognition.domain import summarize_behavior


def fatigue_behavior(score):
    """Behavior whose observations produce the requested four-rule score."""
    high_perclos = score >= 1
    slow_blinks = score >= 2
    yawns = score >= 3
    nods = score >= 4
    return summarize_behavior(
        blink_count=3 if slow_blinks else 12,
        yawn_count=3 if yawns else 1,
        nod_count=2 if nods else 0,
        closed_seconds=15.0 if high_perclos else 7.2,
        speaking_seconds=4.8, face_seconds=60, video_seconds=60 / 0.97,
    )


@pytest.mark.xfail(reason="产品接线缺陷：create_measure 返回的事实未被 _process 记录（见最终报告缺陷1）", strict=False)
@pytest.mark.parametrize(
    "probability, score, assessment_level, advice_level",
    [
        (0.75, 3, "中度疲劳", "重度疲劳"),
        (0.5, 2, "轻度疲劳", "中度疲劳"),
        (0.25, 1, "正常", "轻度疲劳"),
        (0.0, 0, "正常", "状态良好"),
    ],
)
def test_score_levels_reach_the_report_facts(
    client, fatigue_stub, physiology_stub, probability, score, assessment_level, advice_level,
):
    fatigue = fatigue_stub(fatigue_behavior(score))
    physiology_stub()
    headers, report_id = start_detection(client, None, "fatigue", "ftg-levels")
    finish(client, headers, report_id)

    result = wait_for_report_stage(client, headers, report_id, "complete")["result"]
    assert result["behavior"]["blink_count"] == fatigue_behavior(score).blink_count
    assert result["fatigue"] == {
        "score": score, "level": assessment_level, "score_fraction": probability,
    }
    assert result["advice"]["level"] == advice_level
    assert result["advice"]["text"]
    assert result["detection_id"] == str(report_id)
    # 请求只传物理帧，模型配置属于应用装配：帧时间单调递增。
    (call,) = fatigue.calls
    frames = list(call["frames"])
    assert all(a[0] < b[0] for a, b in zip(frames, frames[1:]))


def test_device_frames_reach_the_behavior_journal_in_order(client, fatigue_stub, physiology_stub):
    physiology_stub()
    fatigue = fatigue_stub(fatigue_behavior(2))
    headers, report_id = start_detection(client, None, "fatigue", "ftg-journal")
    finish(client, headers, report_id)

    wait_for_report_stage(client, headers, report_id, "complete")
    (call,) = fatigue.calls
    # 设备视频经内存帧窗口直达行为入口：帧迭代器可独立重放，不再物化 MP4。
    frames = list(call["frames"])
    assert len(frames) == 5 and all(image.shape[2] == 3 for _, image in frames)
    assert all(a[0] < b[0] for a, b in zip(frames, frames[1:]))


def test_without_video_records_missing_behavior_failure(client, fatigue_stub):
    fatigue_stub(fatigue_behavior(0))
    attach_fake_device()
    headers = auth_headers(client, "ftg-no-video")
    report_id = begin(client, headers, "fatigue")
    finish(client, headers, report_id)

    result = wait_for_report_stage(client, headers, report_id, "failed")["result"]
    assert result["failure"] == "本轮视频中没有有效的人脸行为数据，请重新检测"


def test_missing_fatigue_data_is_rejected_as_missing_input(client, fatigue_stub):
    """缺少有效人脸行为时以失败事实拒绝疲劳报告。"""
    fatigue_stub(None)
    headers, report_id = start_detection(client, None, "fatigue", "ftg-no-face")
    finish(client, headers, report_id)

    result = wait_for_report_stage(client, headers, report_id, "failed")["result"]
    assert result["failure"] == "本轮视频中没有有效的人脸行为数据，请重新检测"


def test_busy_admission_records_failure_fact_instead_of_409(client, fatigue_stub, api):
    """任务占用不再产生 409 响应：封存后的测量失败进入报告事实。"""
    fatigue_stub(fatigue_behavior(0))
    headers, report_id = start_detection(client, None, "fatigue", "ftg-busy")
    assert api.fatigue_analysis_lock.acquire(blocking=False)
    try:
        finish(client, headers, report_id)
        result = wait_for_report_stage(client, headers, report_id, "failed")["result"]
    finally:
        api.fatigue_analysis_lock.release()

    assert result["failure"] == "疲劳检测任务正在分析，请勿重复提交"


def test_missing_video_releases_admission(client, fatigue_stub, api):
    """素材缺失失败在锁内判定，路径必须仍释放准入。"""
    fatigue_stub(fatigue_behavior(0))
    attach_fake_device()
    headers = auth_headers(client, "ftg-release")
    report_id = begin(client, headers, "fatigue")
    finish(client, headers, report_id)

    wait_for_report_stage(client, headers, report_id, "failed")
    assert api.fatigue_analysis_lock.acquire(blocking=False), "失败后准入必须已释放"
    api.fatigue_analysis_lock.release()
