"""AC#5: missing secrets are reported by name only, never by value."""

from __future__ import annotations

import pytest

from src.adapters.config import load_settings


def test_load_settings_returns_database_url_when_present():
    settings = load_settings({"DATABASE_URL": "postgresql://example/db"})
    assert settings.database_url == "postgresql://example/db"


def test_load_settings_exits_and_reports_only_variable_name_when_missing(capsys):
    with pytest.raises(SystemExit) as exc_info:
        load_settings({})
    assert exc_info.value.code != 0
    captured = capsys.readouterr()
    assert "DATABASE_URL" in captured.err
    assert "postgresql://" not in captured.err
