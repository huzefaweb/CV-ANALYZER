"""Startup analysis-provider adapter selection (AD-22).

Presence of all four AZURE_OPENAI_* vars selects the Azure OpenAI adapter;
their absence (the V1 default) selects Ollama. Only the active adapter is
ever exercised — never both, never mixed. Mirrors the selection *behavior*
of apps/gateway's conditional `include_router` from Story 1.2; the worker
has no HTTP routes to mount, so selection here is a plain module lookup
instead.
"""

from __future__ import annotations

from types import ModuleType

from ..config import Settings
from . import azure_openai_analysis, ollama_analysis


def get_active_adapter(settings: Settings) -> ModuleType:
    return azure_openai_analysis if settings.azure_openai_configured else ollama_analysis
