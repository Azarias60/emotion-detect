"""各类对象只修改自己的账号内值和引用；不运行会话或制作。"""
from unittest.mock import Mock

import pytest

from server.tests.capabilities.bindings import capability


KINDS = ["configuration", "avatar", "voice", "material"]


@pytest.fixture
def objects(tmp_path):
    def connect():
        cleanup = Mock()
        # 测试侧集合只为接入便利，不要求产品使用同一个 CRUD 服务。
        collections = {kind: capability("objects", kind=kind, database_path=tmp_path / "objects.db",
                                       cleanup=cleanup) for kind in KINDS}
        return collections, cleanup
    return connect


@pytest.mark.parametrize("kind", KINDS)
def test_object_changes_are_scoped_to_the_creating_account(objects, kind):
    collections, _ = objects()
    own = collections[kind]
    created = own.create(1, {"name": "original"})
    own.create(2, {"name": "other"})
    own.update(1, created["id"], {"name": "changed"})
    assert own.get(1, created["id"])["name"] == "changed"
    assert [row["name"] for row in own.list(2)] == ["other"]
    with pytest.raises(LookupError):
        own.update(2, created["id"], {"name": "foreign"})
    with pytest.raises(LookupError):
        own.remove(2, created["id"])


@pytest.mark.parametrize("deleted", KINDS)
def test_removal_changes_only_the_target_object_and_its_own_resources(objects, deleted):
    collections, _ = objects()
    material = collections["material"].create(1, {"name": "source"})
    avatar = collections["avatar"].create(1, {"name": "avatar", "material": material["id"]})
    voice = collections["voice"].create(1, {"name": "voice", "material": material["id"]})
    config = collections["configuration"].create(1, {
        "name": "config", "avatar": avatar["id"], "voice": voice["id"],
    })
    rows = dict(configuration=config, avatar=avatar, voice=voice, material=material)
    collections[deleted].remove(1, rows[deleted]["id"])
    with pytest.raises(LookupError):
        collections[deleted].get(1, rows[deleted]["id"])
    for kind in KINDS:
        if kind != deleted:
            assert collections[kind].get(1, rows[kind]["id"]) == rows[kind]


def test_reference_query_reports_actual_referrers_without_rewriting_them(objects):
    collections, _ = objects()
    avatar = collections["avatar"].create(1, {"name": "avatar"})
    config = collections["configuration"].create(1, {"name": "config", "avatar": avatar["id"]})
    references = collections["avatar"].references(1, avatar["id"])
    assert any(row["kind"] == "configuration" and row["id"] == config["id"] for row in references)
    assert collections["configuration"].get(1, config["id"]) == config


@pytest.mark.parametrize("kind", KINDS)
def test_object_owner_provides_its_current_values_and_changes(objects, kind):
    collections, _ = objects()
    own, changes = collections[kind], []
    stop = own.observe(1, changes.append)
    try:
        assert changes[-1] == own.list(1)
        created = own.create(1, {"name": "before"})
        assert any(row["id"] == created["id"] for row in changes[-1])
        own.update(1, created["id"], {"name": "after"})
        assert next(row for row in changes[-1] if row["id"] == created["id"])["name"] == "after"
        own.remove(1, created["id"])
        assert all(row["id"] != created["id"] for row in changes[-1])
    finally:
        stop()


def test_model_configuration_changes_only_the_target_account(tmp_path):
    config = capability("model_config", database_path=tmp_path / "model.db")
    config.set(1, "key-a")
    config.set(2, "key-b")
    config.set(1, "key-new")
    assert config.read(1) == "key-new"
    assert config.read(2) == "key-b"
