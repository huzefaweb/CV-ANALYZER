"""AC#5: missing secrets are reported by name only, never by value."""

from __future__ import annotations

import pytest

from src.config import DEFAULT_OLLAMA_HOST, DEFAULT_OLLAMA_MODEL, load_settings


def test_load_settings_returns_database_url_when_present():
    settings = load_settings({"WORKER_DATABASE_URL": "postgresql://example/db"})
    assert settings.database_url == "postgresql://example/db"


def test_load_settings_exits_and_reports_only_variable_name_when_missing(capsys):
    with pytest.raises(SystemExit) as exc_info:
        load_settings({})
    assert exc_info.value.code != 0
    captured = capsys.readouterr()
    assert "WORKER_DATABASE_URL" in captured.err
    assert "postgresql://" not in captured.err


def test_ollama_host_defaults_when_absent():
    settings = load_settings({"WORKER_DATABASE_URL": "postgresql://example/db"})
    assert settings.ollama_host == DEFAULT_OLLAMA_HOST
    assert settings.azure_openai_configured is False


def test_ollama_host_override_is_honored():
    settings = load_settings(
        {"WORKER_DATABASE_URL": "postgresql://example/db", "OLLAMA_HOST": "http://localhost:11434"}
    )
    assert settings.ollama_host == "http://localhost:11434"


def test_ollama_model_defaults_when_absent():
    settings = load_settings({"WORKER_DATABASE_URL": "postgresql://example/db"})
    assert settings.ollama_model == DEFAULT_OLLAMA_MODEL


def test_ollama_model_override_is_honored():
    settings = load_settings(
        {"WORKER_DATABASE_URL": "postgresql://example/db", "OLLAMA_MODEL": "qwen2.5:14b-instruct"}
    )
    assert settings.ollama_model == "qwen2.5:14b-instruct"


def test_azure_openai_configured_requires_all_four_vars():
    base = {"WORKER_DATABASE_URL": "postgresql://example/db"}
    partial = {
        **base,
        "AZURE_OPENAI_ENDPOINT": "https://example.openai.azure.com/",
        "AZURE_OPENAI_API_KEY": "key",
    }
    assert load_settings(partial).azure_openai_configured is False

    complete = {
        **partial,
        "AZURE_OPENAI_DEPLOYMENT": "deployment",
        "AZURE_OPENAI_API_VERSION": "2024-10-21",
    }
    assert load_settings(complete).azure_openai_configured is True
