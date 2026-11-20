"""账号模型配置：Key 归账号所有，导览与陪伴在下一轮读取。"""
from server.support.sqlite import open_database

SCHEMA = """
CREATE TABLE IF NOT EXISTS model_configs (
    owner_id INTEGER PRIMARY KEY,
    api_key TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL
);
"""


def init_model_config_database(database_path):
    with open_database(database_path) as connection:
        connection.executescript(SCHEMA)


class ModelConfigService:
    def __init__(self, database_path):
        self._database_path = database_path

    def set(self, owner_id, api_key):
        from server.support.reporting.service import utc_now
        with open_database(self._database_path) as connection:
            connection.execute(
                "INSERT INTO model_configs(owner_id,api_key,updated_at) VALUES(?,?,?)"
                " ON CONFLICT(owner_id) DO UPDATE SET api_key=excluded.api_key,"
                " updated_at=excluded.updated_at",
                (owner_id, api_key, utc_now()))

    def read(self, owner_id):
        with open_database(self._database_path) as connection:
            row = connection.execute(
                "SELECT api_key FROM model_configs WHERE owner_id=?", (owner_id,)).fetchone()
        return row["api_key"] if row is not None else ""


def create_model_config(database_path):
    init_model_config_database(database_path)
    return ModelConfigService(database_path)
