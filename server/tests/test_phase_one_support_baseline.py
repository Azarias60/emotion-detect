"""Retained identity, owner isolation and history operations before migration."""

import pytest
from fastapi.testclient import TestClient

from server import settings
from server.app import create_app
from server.support.identity.assembly import create_identity_dependencies
from server.support.identity.adapters import sqlite as identity_database
from server.support.reporting.adapters.sqlite import SqliteReportRepository
from server.support.reporting.adapters import sqlite as reporting_database


@pytest.fixture
def support_client(monkeypatch, tmp_path):
    database = tmp_path / "phase-one.db"
    identity_database.init_database(database)
    reporting_database.init_database(database)
    from server.tests.runtime_fixture import assembly
    monkeypatch.setattr(assembly.identity, "identity", create_identity_dependencies(database).identity)
    monkeypatch.setattr(assembly.reporting, "reports", SqliteReportRepository(database))
    monkeypatch.setattr(settings, "UPLOAD_DIR", tmp_path / "uploads")
    return TestClient(create_app(assembly), raise_server_exceptions=False)


def _account(client, name):
    credentials = {"username": name, "password": "secret123"}
    assert client.post("/api/v1/auth/register", json={**credentials, "rePassword": "secret123"}).status_code == 200
    response = client.post("/api/v1/auth/login", json=credentials)
    assert response.status_code == 200
    return {"Authorization": "Bearer " + response.json()["data"]["token"]}


@pytest.mark.parametrize("method,path,body", [
    ("GET", "/api/v1/users/me", None),
    ("PATCH", "/api/v1/users/me", {"username": "other"}),
    ("PATCH", "/api/v1/users/me/password", {"password": "secret123", "newPassword": "new-secret", "newRePassword": "new-secret"}),
    ("POST", "/api/v1/users/me/avatar", None),
    ("POST", "/api/v1/auth/logout", None),
    ("GET", "/api/v1/reports", None),
    ("GET", "/api/v1/reports/1", None),
    ("DELETE", "/api/v1/reports/1", None),
])
def test_retained_support_endpoints_require_authentication(support_client, method, path, body):
    response = support_client.request(method, path, json=body)
    assert response.status_code == 401
    assert response.json() == {"detail": "请先登录"}


def test_profile_password_and_avatar_use_authenticated_owner(support_client, tmp_path):
    client = support_client
    headers = _account(client, "baseline-owner")
    owner = client.get("/api/v1/users/me", headers=headers).json()["data"]
    response = client.patch("/api/v1/users/me", headers=headers, json={
        "username": "baseline-owner", "age": 30, "email": "owner@example.com",
        "user_id": 999, "id": 999,
    })
    assert response.status_code == 200
    assert response.json()["data"]["id"] == owner["id"]
    assert response.json()["data"]["age"] == 30
    avatar = client.post("/api/v1/users/me/avatar", headers=headers, files={
        "file": ("avatar.png", b"baseline-avatar", "image/png"),
    })
    assert avatar.status_code == 200
    url = avatar.json()["data"]["url"]
    assert url.startswith(f"/uploads/avatars/{owner['id']}/")
    assert (tmp_path / "uploads" / url.removeprefix("/uploads/")).read_bytes() == b"baseline-avatar"
    assert client.get(url).content == b"baseline-avatar"
    password = client.patch("/api/v1/users/me/password", headers=headers, json={
        "password": "secret123", "newPassword": "new-secret", "newRePassword": "new-secret",
    })
    assert password.status_code == 200
    assert client.post("/api/v1/auth/login", json={"username": "baseline-owner", "password": "new-secret"}).status_code == 200


def test_report_list_detail_and_delete_are_owner_scoped(support_client):
    client = support_client
    owner, other = _account(client, "baseline-first"), _account(client, "baseline-second")
    from server.tests.runtime_fixture import assembly
    owner_id = client.get("/api/v1/users/me", headers=owner).json()["data"]["id"]
    create = getattr(assembly.reporting, "create_report", None)
    assert callable(create), "待实现：报告由所属后端能力建立"
    report_id = create(owner_id, "emotion")["id"]
    assert client.get("/api/v1/reports", headers=other).json()["data"]["items"] == []
    assert client.get(f"/api/v1/reports/{report_id}", headers=other).status_code == 404
    assert client.delete(f"/api/v1/reports/{report_id}", headers=other).status_code == 404
    assert client.get(f"/api/v1/reports/{report_id}", headers=owner).status_code == 200
    assert client.delete(f"/api/v1/reports/{report_id}", headers=owner).status_code == 200
    assert client.get(f"/api/v1/reports/{report_id}", headers=owner).status_code == 404
