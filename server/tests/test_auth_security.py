# -*-coding: utf-8 -*-
"""安全域契约：令牌签发/校验、版本注销、限流与 Argon2 密码哈希。

数据库用例通过 API 走全链路；哈希/令牌用例直击 Identity security adapter。
"""
import sqlite3
import time

import pytest

from server import settings
from server.support.identity.adapters import security
from server.support.identity.adapters import sqlite as identity_database
from server.support.identity.types.errors import InvalidCredentialsError
from server.support.identity.assembly import create_identity_dependencies


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch, client):
    """本文件的 API 用例打到临时库，不碰真实数据库。"""
    from server.support.reporting.adapters import sqlite as db
    from server.support.reporting.adapters.sqlite import SqliteReportRepository
    from server.tests.runtime_fixture import assembly

    database = tmp_path / "auth.db"
    identity_database.init_database(database)
    assembly.identity.identity = create_identity_dependencies(database).identity
    assembly.reporting.reports = SqliteReportRepository(database)
    db.init_database(database)
    yield


def register_and_login(client, username="secuser", password="secret123"):
    body = client.post("/api/v1/auth/register", json={
        "username": username, "password": password, "rePassword": password})
    assert body.status_code == 200, body.text
    login = client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert login.status_code == 200, login.text
    return login.json()["data"]


def test_password_hash_round_trip_and_rejects_legacy_format():
    encoded = security.hash_password("secret123")
    assert encoded.startswith("$argon2id$")
    assert security.verify_password("secret123", encoded)
    assert not security.verify_password("wrong", encoded)
    # 开发期不兼容旧 PBKDF2 密码格式。
    assert not security.verify_password("secret123", "pbkdf2_sha256$310000$abc$def")


def test_token_round_trip_carries_version():
    token = security.create_token(7, "secuser", 3)
    claims = security.decode_token(token)
    assert claims["sub"] == "7"
    assert claims["username"] == "secuser"
    assert claims["ver"] == 3
    assert claims["jti"]


def test_tampered_and_foreign_secret_tokens_rejected(monkeypatch):
    token = security.create_token(1, "u", 0)
    header, payload, signature = token.split(".")
    tampered = f"{header}.{payload}x.{signature}"
    with pytest.raises(ValueError):
        security.decode_token(tampered)
    with pytest.raises(ValueError):
        security.decode_token(token + "x")
    monkeypatch.setattr(settings, "JWT_SECRET", "other-secret")
    with pytest.raises(ValueError):
        security.decode_token(token)


def test_expired_token_rejected(monkeypatch):
    monkeypatch.setattr(settings, "TOKEN_TTL_SECONDS", -10)
    token = security.create_token(1, "u", 0)
    monkeypatch.setattr(settings, "TOKEN_TTL_SECONDS", 43200)
    with pytest.raises(ValueError, match="登录已过期"):
        security.decode_token(token)


def test_alg_confusion_is_rejected():
    import base64
    import json

    import pytest

    def b64(value):
        return base64.urlsafe_b64encode(value).rstrip(b"=").decode()

    header = b64(json.dumps({"alg": "none", "typ": "JWT"}).encode())
    payload = b64(json.dumps({"sub": "1", "ver": 0, "exp": 4102444800}).encode())
    unsigned = f"{header}.{payload}."
    with pytest.raises(ValueError):
        security.decode_token(unsigned)


def test_login_logout_flow_and_version_invalidation(client, monkeypatch):
    data = register_and_login(client)
    token = data["token"]
    headers = {"Authorization": f"Bearer {token}"}
    assert client.get("/api/v1/users/me", headers=headers).status_code == 200

    assert client.post("/api/v1/auth/logout", headers=headers).status_code == 200
    # 旧令牌立即失效（版本不匹配）
    rejected = client.get("/api/v1/users/me", headers=headers)
    assert rejected.status_code == 401
    assert "注销" in rejected.json()["detail"]

    # 重新登录拿到新版本令牌；旧令牌无法再用
    again = client.post("/api/v1/auth/login", json={"username": "secuser", "password": "secret123"})
    new_token = again.json()["data"]["token"]
    assert new_token != token
    assert client.get("/api/v1/users/me", headers={"Authorization": f"Bearer {new_token}"}).status_code == 200
    assert client.get("/api/v1/users/me", headers=headers).status_code == 401


def test_logout_requires_auth(client):
    assert client.post("/api/v1/auth/logout").status_code == 401


def test_validation_errors_never_echo_sensitive_input(client):
    """422 保留官方结构化 detail，但不回显 ``input`` 原始输入。

    锁定的泄露输入（评审复现：缺 rePassword 时 detail[].input 携带整个
    请求体，登录密码过短时回显原值——明文密码不得出现在任何错误响应里）。
    """
    leaked = client.post("/api/v1/auth/register", json={
        "username": "echo", "password": "plain-secret-42"})
    assert leaked.status_code == 422
    assert "plain-secret-42" not in leaked.text
    short = client.post("/api/v1/auth/login", json={
        "username": "echo", "password": "tiny"})
    assert short.status_code == 422
    assert "tiny" not in short.text
    # 字段级定位仍在：缺失的 rePassword 可被定位。
    errors = leaked.json()["detail"]
    assert any(error["loc"][-1] == "rePassword" for error in errors)


def test_login_rate_limit_after_five_failures(client):
    register_and_login(client, username="limited")
    for _ in range(5):
        assert client.post("/api/v1/auth/login", json={
            "username": "limited", "password": "bad-password"}).status_code == 401
    assert client.post("/api/v1/auth/login", json={
        "username": "limited", "password": "secret123"}).status_code == 429
    # 其他用户不受牵连
    register_and_login(client, username="unrelated")


def test_login_limiter_prunes_and_stays_bounded():
    from server.support.identity.service import LoginLimiter

    now = [1000.0]
    limiter = LoginLimiter(now=lambda: now[0])
    for index in range(limiter.MAX_TRACKED + 50):
        limiter.record_failure(f"user-{index}")
    assert len(limiter._failures) <= limiter.MAX_TRACKED, "字典必须有界"

    for _ in range(limiter.MAX_FAILURES):
        limiter.record_failure("victim")
    assert limiter.blocked("victim"), "窗口内失败达上限后锁定"
    assert not limiter.blocked("someone-else")

    now[0] += limiter.WINDOW_SECONDS + 1
    assert not limiter.blocked("victim"), "窗口滑过后解锁"
    assert "user-0" not in limiter._failures, "过期用户被修剪"
    assert "victim" not in limiter._failures


def test_user_schema_initializes_token_version_and_preserves_revocation(tmp_path):
    from server.support.identity.adapters import sqlite as db

    db_path = tmp_path / "users.db"
    db.init_database(db_path)
    with sqlite3.connect(db_path) as connection:
        connection.execute("INSERT INTO users(username,password_hash,created_at,updated_at) VALUES('new','x','t','t')")
        connection.execute("INSERT INTO users(username,password_hash,created_at,updated_at,token_version) VALUES('revoked','x','t','t',2)")
    db.init_database(db_path)
    db.init_database(db_path)  # 重复执行必须幂等
    with sqlite3.connect(db_path) as connection:
        versions = dict(connection.execute("SELECT username, token_version FROM users"))
    assert versions == {"new": 0, "revoked": 2}


def test_authenticate_token_returns_session_with_claim_expiry(client):
    """authenticate_token 返回带 exp 的会话，并保持 principal_from_token 契约。"""
    data = register_and_login(client)
    from server.tests.runtime_fixture import assembly
    identity = assembly.identity.identity
    session = identity.authenticate_token(data["token"])
    assert session.principal == identity.principal_from_token(data["token"])
    claims = security.decode_token(data["token"])
    assert session.expires_at == claims["exp"]
    assert session.expires_at > int(time.time())

    # 无 exp 的手工令牌不构成可到期会话，按无效凭证拒绝。
    import jwt as pyjwt

    no_exp = pyjwt.encode({"sub": "1", "username": "secuser", "iat": int(time.time()), "ver": 0},
                          settings.JWT_SECRET, algorithm="HS256")
    with pytest.raises(InvalidCredentialsError):
        identity.authenticate_token(no_exp)


def test_identity_auth_routes_only_support_register_login_and_logout(client):
    """公开认证能力固定为账户注册、密码登录及登出，避免免认证入口回归。"""
    actual = {path for path in client.app.openapi()["paths"]
              if path.startswith("/api/v1/auth/")}
    assert actual == {
        "/api/v1/auth/register",
        "/api/v1/auth/login",
        "/api/v1/auth/logout",
    }


def test_new_identity_database_has_no_extra_account_category(tmp_path):
    """新库只存真实账号字段，不再为调试账户创建额外身份维度。"""
    import sqlite3
    from server.support.identity.adapters.sqlite import init_database

    path = tmp_path / "identity.sqlite"
    init_database(path)
    with sqlite3.connect(path) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(users)")}
        indexes = {row[1] for row in connection.execute("PRAGMA index_list(users)")}
    assert columns == {
        "id", "username", "password_hash", "gender", "age", "email", "user_pic",
        "created_at", "updated_at", "token_version",
    }
    assert indexes == {"sqlite_autoindex_users_1"}
