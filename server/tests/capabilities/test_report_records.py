"""报告记录的创建、增量修改、归属与删除；数据由测试直接提供。

SQLite 属于记录能力内部；不运行检测调度、计算或模型。
"""
import sqlite3

import pytest

from server.support.identity.adapters import sqlite as identity_database
from server.support.reporting.adapters import sqlite as reporting_database
from server.support.reporting.adapters.sqlite import SqliteReportRepository
from server.support.reporting.service import ReportingService
from server.support.reporting.types.errors import ReportNotFoundError


@pytest.fixture
def reports(tmp_path):
    database = tmp_path / "reports.db"
    identity_database.init_database(database)
    reporting_database.init_database(database)
    with sqlite3.connect(database) as connection:
        connection.executemany(
            "INSERT INTO users(id,username,password_hash,created_at,updated_at) VALUES(?,?,?,'2026-11-17','2026-11-17')",
            [(1, "owner", "unused"), (2, "other", "unused")],
        )
    return ReportingService(SqliteReportRepository(database)), database


def create(service, kind="health", owner=1):
    method = getattr(service, "create_report", None)
    assert callable(method), "待实现：后端识别启动时通过报告能力建立所属报告"
    return method(owner, kind)


def update(service, report_id, facts, owner=1):
    method = getattr(service, "update_report", None)
    assert callable(method), "待实现：所属过程向同一报告增量提供业务事实"
    return method(owner, report_id, facts)


def observe(service, owner, listener):
    method = getattr(service, "observe", None)
    assert callable(method), "待实现：报告能力提供账号范围内的当前事实与后续变化"
    stop = method(owner, listener)
    assert callable(stop), "观察使用期需有结束入口"
    return stop


@pytest.mark.parametrize("kind", ["health", "fatigue", "emotion"])
def test_report_exists_before_results_and_retains_its_account(reports, kind):
    service, _ = reports
    created = create(service, kind)
    detail = service.report_detail(1, created["id"])
    assert detail["type"] == kind
    assert detail["createUserId"] == 1
    assert detail["createTime"]
    assert detail["result"].get("physiology") is None
    with pytest.raises(ReportNotFoundError):
        service.report_detail(2, created["id"])


def test_partial_fact_changes_preserve_other_values_and_the_original_identity(reports):
    service, _ = reports
    created = create(service)
    report_id = created["id"]
    answers = [{"question": "睡眠如何？", "answer": "很好"}]
    physiology = {"heart_rate_bpm": 72, "respiration_rate_per_min": None, "hrv": None}
    update(service, report_id, {"answers": answers})
    update(service, report_id, {"physiology": physiology, "advice": {"text": "疲劳相关建议"}})
    partial = service.report_detail(1, report_id)
    assert partial["result"]["answers"] == answers
    assert partial["result"]["physiology"] == physiology
    update(service, report_id, {"report": {"text": "健康综合解读"}})
    completed = service.report_detail(1, report_id)
    assert completed["id"] == report_id
    assert completed["createTime"] == partial["createTime"]
    assert completed["result"]["answers"] == answers
    assert completed["result"]["physiology"] == physiology
    assert completed["result"]["advice"]["text"] == "疲劳相关建议"
    assert completed["result"]["report"]["text"] == "健康综合解读"


def test_collection_contains_complete_content_beyond_the_old_hundred_row_limit(reports):
    service, _ = reports
    ids = []
    for index in range(105):
        report_id = create(service, "emotion")["id"]
        ids.append(report_id)
        update(service, report_id, {"answers": [{"question": "感受？", "answer": f"记录{index}"}]})
    create(service, owner=2)
    collection = service.list_reports(1)
    assert {row["id"] for row in collection["items"]} == set(ids)
    assert all(row["result"]["answers"] for row in collection["items"])


def test_other_account_cannot_update_or_delete_the_original_report(reports):
    service, _ = reports
    report_id = create(service)["id"]
    with pytest.raises(ReportNotFoundError):
        update(service, report_id, {"report": {"text": "其他账号"}}, owner=2)
    with pytest.raises(ReportNotFoundError):
        service.delete_report(2, report_id)
    assert service.report_detail(1, report_id)["result"].get("report") is None


def test_update_of_a_deleted_record_does_not_create_a_new_record(reports):
    service, _ = reports
    report_id = create(service)["id"]
    service.delete_report(1, report_id)
    try:
        update(service, report_id, {"report": {"text": "迟到结果"}})
    except ReportNotFoundError:
        pass
    with pytest.raises(ReportNotFoundError):
        service.report_detail(1, report_id)


def test_reopening_the_repository_reads_the_same_question_records_and_results(reports):
    service, database = reports
    report_id = create(service)["id"]
    update(service, report_id, {"answers": [{"question": "睡眠？", "answer": "很好"}]})
    expected = service.report_detail(1, report_id)
    reporting_database.init_database(database)
    reopened = ReportingService(SqliteReportRepository(database))
    assert reopened.report_detail(1, report_id) == expected


def test_observation_starts_with_the_existing_account_facts(reports):
    service, _ = reports
    report_id = create(service)["id"]
    create(service, owner=2)
    values = []
    stop = observe(service, 1, values.append)
    try:
        assert {row["id"] for row in values[-1]} == {report_id}
    finally:
        stop()


def test_record_creation_change_and_deletion_are_published_by_the_record_owner(reports):
    service, _ = reports
    values = []
    stop = observe(service, 1, values.append)
    try:
        assert values[-1] == []
        report_id = create(service)["id"]
        assert [row["id"] for row in values[-1]] == [report_id]
        update(service, report_id, {"physiology": {"heart_rate_bpm": 72}})
        assert values[-1][0]["result"]["physiology"]["heart_rate_bpm"] == 72
        service.delete_report(1, report_id)
        assert values[-1] == []
    finally:
        stop()


def test_account_scope_and_observation_release_do_not_change_record_lifetime(reports):
    service, _ = reports
    a, b = [], []
    stop_a = observe(service, 1, a.append)
    stop_b = observe(service, 2, b.append)
    try:
        report_id = create(service)["id"]
        assert b[-1] == []
        stop_a()
        count = len(a)
        update(service, report_id, {"answers": [{"question": "问题", "answer": "回答"}]})
        assert len(a) == count
        assert service.report_detail(1, report_id)["result"]["answers"]
        renewed = []
        stop = observe(service, 1, renewed.append)
        try:
            assert renewed[-1][0]["result"]["answers"]
        finally:
            stop()
    finally:
        stop_b()
