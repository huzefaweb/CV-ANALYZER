"""Failure-category mapping tests for OllamaAnalysisAdapter (AC#4).

Unit tests below monkeypatch httpx.post to deterministically exercise each
of the five frozen failure categories without a live Ollama server. See
test_ollama_analysis_live.py for the live-smoke suite (AC#1-#3), which
requires the docker compose `ollama` service running with
qwen2.5:0.5b-instruct pulled.
"""

from __future__ import annotations

import json

import httpx
import pytest

from src.adapters import ollama_analysis
from src.domain.analysis_provider import AnalysisProviderError, JobRequirement, ResumeSourceUnit
from src.domain.question_context import GroundedRequirement

REQUIREMENTS = [JobRequirement(id="JR-1", text="Python experience")]
UNITS = [ResumeSourceUnit(id="unit-1", text="5 years of Python.")]


def _propose():
    return ollama_analysis.propose(REQUIREMENTS, UNITS, base_url="http://ollama.invalid:11434")


def test_timeout_maps_to_analysis_timed_out(monkeypatch):
    def raise_timeout(*args, **kwargs):
        raise httpx.TimeoutException("timed out")

    monkeypatch.setattr(httpx, "post", raise_timeout)
    with pytest.raises(AnalysisProviderError) as exc_info:
        _propose()
    assert exc_info.value.category == "Analysis timed out"


def test_connection_error_maps_to_service_unavailable(monkeypatch):
    def raise_connect_error(*args, **kwargs):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(httpx, "post", raise_connect_error)
    with pytest.raises(AnalysisProviderError) as exc_info:
        _propose()
    assert exc_info.value.category == "Analysis service unavailable"


def test_http_5xx_maps_to_service_unavailable(monkeypatch):
    def fake_post(*args, **kwargs):
        return httpx.Response(503, request=httpx.Request("POST", "http://x"))

    monkeypatch.setattr(httpx, "post", fake_post)
    with pytest.raises(AnalysisProviderError) as exc_info:
        _propose()
    assert exc_info.value.category == "Analysis service unavailable"


def test_malformed_json_body_maps_to_response_could_not_be_validated(monkeypatch):
    def fake_post(*args, **kwargs):
        return httpx.Response(200, content=b"not json", request=httpx.Request("POST", "http://x"))

    monkeypatch.setattr(httpx, "post", fake_post)
    with pytest.raises(AnalysisProviderError) as exc_info:
        _propose()
    assert exc_info.value.category == "Analysis response could not be validated"


def test_schema_invalid_content_maps_to_response_could_not_be_validated(monkeypatch):
    def fake_post(*args, **kwargs):
        body = {"message": {"content": json.dumps({"items": [{"job_requirement_id": "JR-1", "state": "Yes"}]})}}
        return httpx.Response(200, json=body, request=httpx.Request("POST", "http://x"))

    monkeypatch.setattr(httpx, "post", fake_post)
    with pytest.raises(AnalysisProviderError) as exc_info:
        _propose()
    assert exc_info.value.category == "Analysis response could not be validated"


def test_incomplete_coverage_maps_to_response_could_not_be_validated(monkeypatch):
    def fake_post(*args, **kwargs):
        # Well-formed items, but missing JR-1 entirely.
        body = {"message": {"content": json.dumps({"items": []})}}
        return httpx.Response(200, json=body, request=httpx.Request("POST", "http://x"))

    monkeypatch.setattr(httpx, "post", fake_post)
    with pytest.raises(AnalysisProviderError) as exc_info:
        _propose()
    assert exc_info.value.category == "Analysis response could not be validated"


def test_empty_content_maps_to_automated_analysis_unavailable(monkeypatch):
    def fake_post(*args, **kwargs):
        body = {"message": {"content": "   "}}
        return httpx.Response(200, json=body, request=httpx.Request("POST", "http://x"))

    monkeypatch.setattr(httpx, "post", fake_post)
    with pytest.raises(AnalysisProviderError) as exc_info:
        _propose()
    assert exc_info.value.category == "Automated analysis unavailable for this document"


def test_remote_protocol_error_maps_to_document_processing_interrupted(monkeypatch):
    def raise_protocol_error(*args, **kwargs):
        raise httpx.RemoteProtocolError("connection dropped mid-response")

    monkeypatch.setattr(httpx, "post", raise_protocol_error)
    with pytest.raises(AnalysisProviderError) as exc_info:
        _propose()
    assert exc_info.value.category == "Document processing interrupted"


def test_successful_well_formed_response_returns_proposal(monkeypatch):
    def fake_post(*args, **kwargs):
        body = {
            "message": {
                "content": json.dumps(
                    {"items": [{"job_requirement_id": "JR-1", "state": "Matched", "locator": "unit-1", "excerpt": "5 years"}]}
                )
            }
        }
        return httpx.Response(200, json=body, request=httpx.Request("POST", "http://x"))

    monkeypatch.setattr(httpx, "post", fake_post)
    proposal = _propose()
    assert proposal.items[0].job_requirement_id == "JR-1"
    assert proposal.items[0].state.value == "Matched"


def test_no_raw_provider_detail_leaks_into_category(monkeypatch):
    def fake_post(*args, **kwargs):
        return httpx.Response(
            500, content=b"internal traceback: secret-model-v7 quota exceeded", request=httpx.Request("POST", "http://x")
        )

    monkeypatch.setattr(httpx, "post", fake_post)
    with pytest.raises(AnalysisProviderError) as exc_info:
        _propose()
    assert "secret-model-v7" not in str(exc_info.value)
    assert "traceback" not in str(exc_info.value)


# Story 7.1 code review addition (Acceptance Auditor, High): AC#2 is
# entirely about the constructed provider request, and nothing in the
# original diff inspected it. Mirrors _build_user_message having no
# dedicated construction test either — this closes the analogous gap for
# the new question-generation prompt at the same (cheap, structural) level.
def test_question_user_message_carries_only_grounded_fields_fenced_as_untrusted():
    grounded = [
        GroundedRequirement(
            job_requirement_id="JR-1",
            requirement_text="Kubernetes operations",
            state="Matched",
            locator_description="Page 2",
            excerpt="Led Kubernetes cluster operations",
        ),
        GroundedRequirement(
            job_requirement_id="JR-2",
            requirement_text="Kafka production operations",
            state="Not Found",
            locator_description=None,
            excerpt="",
        ),
    ]
    message = ollama_analysis._build_question_user_message(grounded)
    assert "<grounded_context>" in message and "</grounded_context>" in message
    assert "JR-1" in message and "Kubernetes operations" in message and "Page 2" in message
    assert "JR-2" in message and "Not Found" in message
    # Never a name/email/phone/identity field — GroundedRequirement itself
    # has no such field, so this is a structural guarantee, not luck; this
    # assertion documents that invariant explicitly.
    assert "@" not in message


def test_question_system_prompt_frames_evidence_excerpts_as_untrusted_data():
    prompt = ollama_analysis._QUESTION_SYSTEM_PROMPT
    assert "ignore" in prompt.lower() and "excerpt" in prompt.lower()
    assert "ten" in prompt.lower()
    assert "technical_functional" in prompt and "gap_focused" in prompt
