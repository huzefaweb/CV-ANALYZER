"""AC#1: the Azure OpenAI adapter is a deferred, non-blocking stub (AD-22)."""

from __future__ import annotations

import pytest

from src.adapters.azure_openai_analysis import AzureOpenAINotConfigured, propose
from src.domain.analysis_provider import JobRequirement, ResumeSourceUnit


def test_propose_raises_deferred_not_configured():
    with pytest.raises(AzureOpenAINotConfigured):
        propose(
            [JobRequirement(id="JR-1", text="x")],
            [ResumeSourceUnit(id="unit-1", text="y")],
            base_url="https://example.openai.azure.com/",
        )
