"""SQLite 用户 Repository。"""

import sqlite3
from pathlib import Path

from server.support.identity.types.errors import UsernameTakenError
from server.support.sqlite import open_database


def init_database(database_path):
    Path(database_path).parent.mkdir(parents=True, exist_ok=True)
    with open_database(database_path) as connection:
        connection.execute(
            """CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                gender TEXT DEFAULT '', age INTEGER, email TEXT DEFAULT '',
                user_pic TEXT DEFAULT '', created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL, token_version INTEGER NOT NULL DEFAULT 0
            )"""
        )


class SqliteUserRepository:
    def __init__(self, database_path):
        self.database_path = database_path

    def create(self, username, password_hash, created_at):
        with open_database(self.database_path) as connection:
            try:
                cursor = connection.execute(
                    "INSERT INTO users(username,password_hash,created_at,updated_at) VALUES(?,?,?,?)",
                    (username, password_hash, created_at, created_at),
                )
            except sqlite3.IntegrityError as error:
                raise UsernameTakenError("用户名已存在") from error
            return cursor.lastrowid

    def get_by_username(self, username):
        with open_database(self.database_path) as connection:
            row = connection.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
        return dict(row) if row else None

    def get_by_id(self, user_id):
        with open_database(self.database_path) as connection:
            row = connection.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        return dict(row) if row else None

    def update_profile(self, user_id, values, updated_at):
        with open_database(self.database_path) as connection:
            try:
                connection.execute(
                    "UPDATE users SET username=?,gender=?,age=?,email=?,user_pic=?,updated_at=? WHERE id=?",
                    (values["username"], values["gender"], values["age"], values["email"], values["user_pic"], updated_at, user_id),
                )
            except sqlite3.IntegrityError as error:
                raise UsernameTakenError("用户名已存在") from error
            row = connection.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        return dict(row) if row else None

    def update_password(self, user_id, password_hash, updated_at):
        with open_database(self.database_path) as connection:
            connection.execute("UPDATE users SET password_hash=?,updated_at=? WHERE id=?", (password_hash, updated_at, user_id))

    def increment_token_version(self, user_id):
        with open_database(self.database_path) as connection:
            connection.execute("UPDATE users SET token_version=token_version+1 WHERE id=?", (user_id,))
