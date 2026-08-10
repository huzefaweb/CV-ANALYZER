"""AD-22: startup adapter selection follows AZURE_OPENAI_* presence/absence exactly."""

from __future__ import annotations

from src.adapters import azure_openai_analysis, ollama_analysis
from src.adapters.analysis_selection import get_active_adapter
from src.config import load_settings


def test_absent_azure_vars_selects_ollama():
    settings = load_settings({"WORKER_DATABASE_URL": "postgresql://example/db"})
    assert get_active_adapter(settings) is ollama_analysis


def test_all_four_azure_vars_present_selects_azure():
    settings = load_settings(
        {
            "WORKER_DATABASE_URL": "postgresql://example/db",
            "AZURE_OPENAI_ENDPOINT": "https://example.openai.azure.com/",
            "AZURE_OPENAI_API_KEY": "key",
            "AZURE_OPENAI_DEPLOYMENT": "deployment",
            "AZURE_OPENAI_API_VERSION": "2024-10-21",
        }
    )
    assert get_active_adapter(settings) is azure_openai_analysis


def test_partial_azure_vars_still_selects_ollama():
    settings = load_settings(
        {
            "WORKER_DATABASE_URL": "postgresql://example/db",
            "AZURE_OPENAI_ENDPOINT": "https://example.openai.azure.com/",
            "AZURE_OPENAI_API_KEY": "key",
        }
    )
    assert get_active_adapter(settings) is ollama_analysis
