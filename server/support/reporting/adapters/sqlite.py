"""Reporting 的 SQLite 适配器：连接、schema 与报告存取 SQL。

数据库路径由装配根统一注入（与身份库同源），仓库与初始化器都只使用
构造/调用时收到的路径，不持有模块级全局状态。报告只保留业务事实：
标识、类型、归属账号、结果与时间；标题、摘要等展示内容由显示端派生。
"""
import json
from pathlib import Path

from server.support.sqlite import open_database


def init_database(database_path):
    Path(database_path).parent.mkdir(parents=True, exist_ok=True)
    with open_database(database_path) as connection:
        columns = {row["name"] for row in connection.execute("PRAGMA table_info(reports)")}
        if columns and "title" in columns:
            _migrate_legacy_columns(connection, columns)
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS reports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                create_user_id INTEGER NOT NULL,
                report_type TEXT NOT NULL,
                result_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(create_user_id) REFERENCES users(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_reports_user_time
                ON reports(create_user_id, created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_reports_user_type
                ON reports(create_user_id, report_type);
            """
        )


def _migrate_legacy_columns(connection, columns):
    """收敛旧版报告表：报告类型固定为三类，展示副本列移除。

    旧的保存型综合分析与舌象/对话条目不再是报告类型，随迁移删除；
    报告正文已随 result_json 保留在结果事实中，rawReport 复制路径不再存在。
    """
    connection.execute(
        "DELETE FROM reports WHERE report_type NOT IN ('health','fatigue','emotion')")
    if "idx_reports_idempotency" in {
        row["name"] for row in connection.execute("PRAGMA index_list(reports)")
    }:
        connection.execute("DROP INDEX idx_reports_idempotency")
    for column in ("title", "summary", "report_text", "file_address", "idempotency_key", "request_hash"):
        if column in columns:
            connection.execute(f"ALTER TABLE reports DROP COLUMN {column}")


class SqliteReportRepository:
    """报告存取 SQL；每次操作用构造注入的路径开独立连接。"""

    def __init__(self, database_path):
        self._database_path = database_path

    def list_reports(self, owner_id):
        """账号全量报告，按创建时间由近到远。"""
        with open_database(self._database_path) as connection:
            return connection.execute(
                "SELECT * FROM reports WHERE create_user_id=? ORDER BY created_at DESC, id DESC",
                (owner_id,)).fetchall()

    def get_report(self, owner_id, report_id):
        with open_database(self._database_path) as connection:
            return connection.execute(
                "SELECT * FROM reports WHERE id=? AND create_user_id=?", (report_id, owner_id)).fetchone()

    def get_report_by_id(self, report_id):
        """后端所属过程按报告标识读取；归属账号在行内，访问控制由调用方绑定保证。"""
        with open_database(self._database_path) as connection:
            return connection.execute(
                "SELECT * FROM reports WHERE id=?", (report_id,)).fetchone()

    def insert_report(self, owner_id, report_type, result_json, now):
        with open_database(self._database_path) as connection:
            cursor = connection.execute(
                "INSERT INTO reports(create_user_id,report_type,result_json,created_at,updated_at)"
                " VALUES(?,?,?,?,?)",
                (owner_id, report_type, result_json, now, now),
            )
            return {"id": cursor.lastrowid}

    def update_facts(self, report_id, facts, now, owner_id=None):
        """原子合并增量事实（顶层键整体替换，其余保留）；返回归属账号。

        ``owner_id`` 提供时校验归属（账号侧入口）；后端所属过程按报告
        标识写入，归属由行内 create_user_id 返回。
        """
        with open_database(self._database_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            query = "SELECT create_user_id, result_json FROM reports WHERE id=?"
            params = [report_id]
            if owner_id is not None:
                query += " AND create_user_id=?"
                params.append(owner_id)
            row = connection.execute(query, params).fetchone()
            if row is None:
                connection.rollback()
                return None
            merged = {**json.loads(row["result_json"]), **facts}
            connection.execute(
                "UPDATE reports SET result_json=?, updated_at=? WHERE id=?",
                (json.dumps(merged, ensure_ascii=False), now, report_id),
            )
            return row["create_user_id"]

    def delete_report(self, owner_id, report_id):
        with open_database(self._database_path) as connection:
            return connection.execute(
                "DELETE FROM reports WHERE id=? AND create_user_id=?", (report_id, owner_id)).rowcount
