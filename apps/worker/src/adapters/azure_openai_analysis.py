"""AzureOpenAIAnalysisAdapter (AD-22): deferred until AZURE_OPENAI_* env vars
are configured.

Mirrors ollama_analysis's function name/arity (ANALYSIS_PROVIDER_FUNCTIONS in
src/domain/analysis_provider.py) so switching the active adapter later needs
no downstream rework. Not wired to a live Azure OpenAI deployment in this
story — AC#1 explicitly defers that preflight rather than failing the story
over unconfigured credentials. The function raises unconditionally: nothing
calls this module while AZURE_OPENAI_* env vars are absent.
"""

from __future__ import annotations

from ..domain.analysis_provider import JobRequirement, ResumeSourceUnit


class AzureOpenAINotConfigured(Exception):
    """Raised if the Azure OpenAI adapter is called before its deferred preflight runs."""


def propose(
    requirements: list[JobRequirement],
    source_units: list[ResumeSourceUnit],
    *,
    base_url: str,
    model: str = "",
    timeout: float = 60.0,
):
    raise AzureOpenAINotConfigured(
        "Azure OpenAI adapter preflight is deferred (AC#1) until AZURE_OPENAI_* env vars are configured."
    )
