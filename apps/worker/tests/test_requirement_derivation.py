"""Failure-category mapping tests for OllamaAnalysisAdapter's
derive_requirements (AD-4) — mirrors test_ollama_analysis.py's propose()
coverage for the same five frozen failure categories.
"""

from __future__ import annotations

import json

import httpx
import pytest

from src.adapters import ollama_analysis
from src.domain.analysis_provider import AnalysisProviderError

JOB_DESCRIPTION_TEXT = "We need a Python engineer with 5+ years experience."


def _derive():
    return ollama_analysis.derive_requirements(
        JOB_DESCRIPTION_TEXT, base_url="http://ollama.invalid:11434", model="qwen2.5:0.5b-instruct"
    )


def test_timeout_maps_to_analysis_timed_out(monkeypatch):
    def raise_timeout(*args, **kwargs):
        raise httpx.TimeoutException("timed out")

    monkeypatch.setattr(httpx, "post", raise_timeout)
    with pytest.raises(AnalysisProviderError) as exc_info:
        _derive()
    assert exc_info.value.category == "Analysis timed out"


def test_connection_error_maps_to_service_unavailable(monkeypatch):
    def raise_connect_error(*args, **kwargs):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(httpx, "post", raise_connect_error)
    with pytest.raises(AnalysisProviderError) as exc_info:
        _derive()
    assert exc_info.value.category == "Analysis service unavailable"


def test_malformed_json_body_maps_to_response_could_not_be_validated(monkeypatch):
    def fake_post(*args, **kwargs):
        return httpx.Response(200, content=b"not json", request=httpx.Request("POST", "http://x"))

    monkeypatch.setattr(httpx, "post", fake_post)
    with pytest.raises(AnalysisProviderError) as exc_info:
        _derive()
    assert exc_info.value.category == "Analysis response could not be validated"


def test_unsupported_component_maps_to_response_could_not_be_validated(monkeypatch):
    def fake_post(*args, **kwargs):
        body = {
            "message": {
                "content": json.dumps(
                    {
                        "items": [
                            {
                                "component": "not_a_real_component",
                                "classification": "mandatory",
                                "text": "Python",
                                "source_start": 0,
                                "source_end": 6,
                            }
                        ]
                    }
                )
            }
        }
        return httpx.Response(200, json=body, request=httpx.Request("POST", "http://x"))

    monkeypatch.setattr(httpx, "post", fake_post)
    with pytest.raises(AnalysisProviderError) as exc_info:
        _derive()
    assert exc_info.value.category == "Analysis response could not be validated"


def test_out_of_bounds_locator_maps_to_response_could_not_be_validated(monkeypatch):
    def fake_post(*args, **kwargs):
        body = {
            "message": {
                "content": json.dumps(
                    {
                        "items": [
                            {
                                "component": "mandatory_skills",
                                "classification": "mandatory",
                                "text": "Python",
                                "source_start": 0,
                                "source_end": 9999,
                            }
                        ]
                    }
                )
            }
        }
        return httpx.Response(200, json=body, request=httpx.Request("POST", "http://x"))

    monkeypatch.setattr(httpx, "post", fake_post)
    with pytest.raises(AnalysisProviderError) as exc_info:
        _derive()
    assert exc_info.value.category == "Analysis response could not be validated"


def test_empty_content_maps_to_automated_analysis_unavailable(monkeypatch):
    def fake_post(*args, **kwargs):
        body = {"message": {"content": "   "}}
        return httpx.Response(200, json=body, request=httpx.Request("POST", "http://x"))

    monkeypatch.setattr(httpx, "post", fake_post)
    with pytest.raises(AnalysisProviderError) as exc_info:
        _derive()
    assert exc_info.value.category == "Automated analysis unavailable for this document"


def test_successful_well_formed_response_returns_proposal(monkeypatch):
    def fake_post(*args, **kwargs):
        body = {
            "message": {
                "content": json.dumps(
                    {
                        "items": [
                            {
                                "component": "mandatory_skills",
                                "classification": "mandatory",
                                "text": "Python",
                                "source_start": 11,
                                "source_end": 17,
                            }
                        ]
                    }
                )
            }
        }
        return httpx.Response(200, json=body, request=httpx.Request("POST", "http://x"))

    monkeypatch.setattr(httpx, "post", fake_post)
    proposal = _derive()
    assert proposal.items[0].component == "mandatory_skills"
    assert proposal.items[0].classification == "mandatory"
