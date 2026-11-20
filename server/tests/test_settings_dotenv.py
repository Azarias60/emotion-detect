"""Configuration must load .env before resolving derived paths."""

import runpy
from pathlib import Path

import dotenv


def test_dotenv_data_dir_is_used_for_default_database_and_upload_paths(monkeypatch, tmp_path):
    monkeypatch.delenv("EMOTION_DATA_DIR", raising=False)
    monkeypatch.delenv("EMOTION_DATABASE_PATH", raising=False)
    monkeypatch.delenv("EMOTION_UPLOAD_DIR", raising=False)

    def fake_load_dotenv(path):
        assert Path(path).name == ".env"
        monkeypatch.setenv("EMOTION_DATA_DIR", str(tmp_path / "from-dotenv"))

    monkeypatch.setattr(dotenv, "load_dotenv", fake_load_dotenv)
    settings = runpy.run_path(str(Path(__file__).resolve().parents[1] / "settings.py"))

    assert settings["DATA_DIR"] == str(tmp_path / "from-dotenv")
    assert settings["DATABASE_PATH"] == str(tmp_path / "from-dotenv" / "emotion_management.db")
    assert settings["UPLOAD_DIR"] == str(tmp_path / "from-dotenv" / "uploads")
