"""AD-22: Ollama and Azure OpenAI adapter modules must expose the same
`propose_questions` function contract (QUESTION_PROVIDER_FUNCTIONS), mirroring
test_analysis_provider_port.py's identical convention for `propose`.
"""

from __future__ import annotations

import inspect

from src.adapters import azure_openai_analysis, ollama_analysis
from src.domain.question_provider import QUESTION_PROVIDER_FUNCTIONS


def test_both_adapters_expose_the_same_port_functions():
    for name in QUESTION_PROVIDER_FUNCTIONS:
        assert hasattr(ollama_analysis, name), f"ollama_analysis missing '{name}'"
        assert hasattr(azure_openai_analysis, name), f"azure_openai_analysis missing '{name}'"


def test_both_adapters_agree_on_function_arity():
    for name in QUESTION_PROVIDER_FUNCTIONS:
        ollama_params = list(inspect.signature(getattr(ollama_analysis, name)).parameters)
        azure_params = list(inspect.signature(getattr(azure_openai_analysis, name)).parameters)
        assert ollama_params == azure_params, (
            f"'{name}' signature mismatch: ollama={ollama_params} azure={azure_params}"
        )
