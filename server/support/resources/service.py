"""账号内的独立对象集合：配置、形象、音色、素材各自拥有事实与变化。

每类对象按账号隔离 CRUD 与观察；删除只结束目标对象及其自身资源，
引用者保留原引用，引用查询报告实际引用者。物理资源清理回调由装配
注入，按对象自身携带的资源路径调用。
"""
import json
import threading

from server.support.sqlite import open_database

# 引用关系：被引用 kind → 引用它的 kind 与其值键。
REFERENCE_SOURCES = {
    "avatar": (("configuration", "avatar"),),
    "voice": (("configuration", "voice"),),
    "material": (("avatar", "material"), ("voice", "material")),
    "configuration": (),
}

# 对象自身携带的物理资源键；删除时连同资源一起结束。
OWN_RESOURCE_KEYS = {
    "avatar": ("material",),
    "voice": ("material",),
    "material": ("path",),
    "configuration": (),
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS objects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    create_user_id INTEGER NOT NULL,
    kind TEXT NOT NULL,
    values_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_objects_user_kind
    ON objects(create_user_id, kind, id DESC);
"""


def init_objects_database(database_path):
    with open_database(database_path) as connection:
        connection.executescript(SCHEMA)


def _serialize(row):
    return {**json.loads(row["values_json"]), "id": row["id"]}


class _KindCollection:
    def __init__(self, database_path, kind, cleanup, observers, lock):
        self._database_path = database_path
        self._kind = kind
        self._cleanup = cleanup
        self._observers = observers
        self._lock = lock

    def _publish(self, owner_id):
        rows = self.list(owner_id)
        with self._lock:
            listeners = tuple(self._observers.get(
                _observers_key(self._database_path, self._kind, owner_id), ()))
        for listener in listeners:
            listener(rows)

    def create(self, owner_id, values):
        from server.support.reporting.service import utc_now
        now = utc_now()
        with open_database(self._database_path) as connection:
            cursor = connection.execute(
                "INSERT INTO objects(create_user_id,kind,values_json,created_at,updated_at)"
                " VALUES(?,?,?,?,?)",
                (owner_id, self._kind, json.dumps(values, ensure_ascii=False), now, now),
            )
            row_id = cursor.lastrowid
        self._publish(owner_id)
        return {**values, "id": row_id}

    def _fetch(self, owner_id, object_id):
        with open_database(self._database_path) as connection:
            return connection.execute(
                "SELECT * FROM objects WHERE id=? AND create_user_id=? AND kind=?",
                (object_id, owner_id, self._kind)).fetchone()

    def get(self, owner_id, object_id):
        row = self._fetch(owner_id, object_id)
        if row is None:
            raise LookupError("对象不存在")
        return _serialize(row)

    def list(self, owner_id):
        with open_database(self._database_path) as connection:
            rows = connection.execute(
                "SELECT * FROM objects WHERE create_user_id=? AND kind=? ORDER BY id DESC",
                (owner_id, self._kind)).fetchall()
        return [_serialize(row) for row in rows]

    def update(self, owner_id, object_id, values):
        from server.support.reporting.service import utc_now
        row = self._fetch(owner_id, object_id)
        if row is None:
            raise LookupError("对象不存在")
        merged = {**json.loads(row["values_json"]), **values}
        with open_database(self._database_path) as connection:
            connection.execute(
                "UPDATE objects SET values_json=?, updated_at=? WHERE id=?",
                (json.dumps(merged, ensure_ascii=False), utc_now(), object_id))
        self._publish(owner_id)

    def remove(self, owner_id, object_id):
        row = self._fetch(owner_id, object_id)
        if row is None:
            raise LookupError("对象不存在")
        with open_database(self._database_path) as connection:
            connection.execute("DELETE FROM objects WHERE id=?", (object_id,))
        self._publish(owner_id)
        values = json.loads(row["values_json"])
        paths = [values[key] for key in OWN_RESOURCE_KEYS.get(self._kind, ())
                 if values.get(key)]
        if paths and self._cleanup is not None:
            self._cleanup(paths)

    def references(self, owner_id, object_id):
        """实际引用者查询：只报告真实引用，不改写被引用对象。"""
        found = []
        with open_database(self._database_path) as connection:
            for source_kind, key in REFERENCE_SOURCES.get(self._kind, ()):
                rows = connection.execute(
                    "SELECT * FROM objects WHERE create_user_id=? AND kind=? ORDER BY id DESC",
                    (owner_id, source_kind)).fetchall()
                for row in rows:
                    values = json.loads(row["values_json"])
                    if values.get(key) == object_id:
                        found.append({"kind": source_kind, "id": row["id"], "name": values.get("name", "")})
        return found

    def observe(self, owner_id, listener):
        key = _observers_key(self._database_path, self._kind, owner_id)
        with self._lock:
            self._observers.setdefault(key, set()).add(listener)
        listener(self.list(owner_id))

        def stop():
            with self._lock:
                listeners = self._observers.get(key)
                if listeners is not None:
                    listeners.discard(listener)
                    if not listeners:
                        self._observers.pop(key, None)
        return stop


def create_objects(kind, database_path, cleanup=None):
    """装配一类账号内对象集合；同一数据库的各类集合共享观察登记。"""
    init_objects_database(database_path)
    return _KindCollection(database_path, kind, cleanup, _OBSERVERS, _OBSERVERS_LOCK)


_OBSERVERS: dict = {}
_OBSERVERS_LOCK = threading.Lock()


def _observers_key(database_path, kind, owner_id):
    return (str(database_path), kind, owner_id)
