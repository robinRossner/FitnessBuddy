"""The real `anthropic` SDK against a mock HTTP transport (no network): the request the advisor
sends and how refusals, cut-offs and HTTP errors come back."""

from __future__ import annotations

import json
import logging

import anthropic
import httpx2
import pytest

from app.advisor import claude
from app.advisor.schema import AdvisorOutput
from app.settings import Settings
from app.sources.garmin.redact import redact

ANSWER = {
    "diagnosis": "Fine.",
    "proposed_changes": [
        {
            "param": "strength.rep_range",
            "from": [7, 12],
            "to": [8, 12],
            "reason": "r",
            "risk": "low",
        }
    ],
    "session_overrides": [],
    "confidence": "high",
    "notes_for_user": "Keep going.",
}


def message(stop_reason="end_turn", text=None, **usage):
    return {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "model": "claude-opus-5-5",
        "content": [{"type": "text", "text": json.dumps(ANSWER if text is None else text)}]
        if stop_reason == "end_turn"
        else [],
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "stop_details": {"category": "cyber", "explanation": "no"}
        if stop_reason == "refusal"
        else None,
        "usage": {
            "input_tokens": 100,
            "output_tokens": 50,
            "cache_read_input_tokens": 10,
            "cache_creation_input_tokens": 20,
            **usage,
        },
    }


def sdk_client(handler, **kw):
    return anthropic.Anthropic(
        api_key="sk-ant-test-0000000000000000",
        max_retries=kw.pop("max_retries", 0),
        http_client=anthropic.DefaultHttpxClient(transport=httpx2.MockTransport(handler)),
        **kw,
    )


@pytest.fixture
def settings():
    return Settings(_env_file=None, anthropic_api_key="sk-ant-test-0000000000000000")


def test_the_wire_request(settings):
    seen = {}

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen["body"] = json.loads(request.content)
        seen["headers"] = request.headers
        seen["url"] = str(request.url)
        return httpx2.Response(200, json=message())

    result = claude.consult(settings, "<input>{}</input>", client=sdk_client(handler))
    body = seen["body"]
    assert seen["url"].endswith("/v1/messages")  # non-streaming
    assert body["model"] == "claude-opus-5-5" and body["max_tokens"] == 16000
    assert body["thinking"] == {"type": "adaptive"}
    assert body["output_config"]["effort"] == "high"
    fmt = body["output_config"]["format"]
    assert fmt["type"] == "json_schema"
    assert set(fmt["schema"]["required"]) == {
        "diagnosis",
        "proposed_changes",
        "session_overrides",
        "confidence",
        "notes_for_user",
    }
    assert body["fallbacks"] == "default"
    assert "server-side-fallback-2026-07-01" in seen["headers"]["anthropic-beta"]
    assert body["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert body["messages"] == [{"role": "user", "content": "<input>{}</input>"}]
    for forbidden in ("tool_choice", "stream", "temperature", "budget_tokens"):
        assert forbidden not in json.dumps(body)

    assert result.outcome == "ok" and isinstance(result.output, AdvisorOutput)
    assert result.output.proposed_changes[0].from_ == [7, 12]
    assert result.usage == {
        "input_tokens": 100,
        "output_tokens": 50,
        "cache_read_input_tokens": 10,
        "cache_creation_input_tokens": 20,
    }


def test_refusal_and_max_tokens_are_checked_before_content(settings):
    refused = claude.consult(
        settings, "x", client=sdk_client(lambda r: httpx2.Response(200, json=message("refusal")))
    )
    assert refused.outcome == "refusal" and refused.output is None
    assert refused.stop_details["category"] == "cyber"
    cut = claude.consult(
        settings, "x", client=sdk_client(lambda r: httpx2.Response(200, json=message("max_tokens")))
    )
    assert cut.outcome == "max_tokens" and cut.output is None


def test_http_errors_become_advisor_call_errors(settings):
    def status(code):
        return sdk_client(
            lambda r: httpx2.Response(
                code,
                json={
                    "type": "error",
                    "error": {
                        "type": "rate_limit_error" if code == 429 else "api_error",
                        "message": "slow down",
                    },
                },
            )
        )

    with pytest.raises(claude.AdvisorCallError) as rate:
        claude.consult(settings, "x", client=status(429))
    assert rate.value.kind == "rate_limit" and rate.value.status_code == 429
    with pytest.raises(claude.AdvisorCallError) as server:
        claude.consult(settings, "x", client=status(500))
    assert server.value.kind == "api_status" and server.value.status_code == 500

    def down(request):
        raise httpx2.ConnectError("no route", request=request)

    with pytest.raises(claude.AdvisorCallError) as conn:
        claude.consult(settings, "x", client=sdk_client(down))
    assert conn.value.kind == "connection"


def test_the_sdk_retries_429_with_backoff(settings):
    calls = []

    def handler(request):
        calls.append(1)
        if len(calls) < 3:
            return httpx2.Response(
                429,
                headers={"retry-after-ms": "1"},
                json={
                    "type": "error",
                    "error": {"type": "rate_limit_error", "message": "slow down"},
                },
            )
        return httpx2.Response(200, json=message())

    result = claude.consult(settings, "x", client=sdk_client(handler, max_retries=3))
    assert result.outcome == "ok" and len(calls) == 3


def test_invalid_json_from_the_model_is_an_invalid_output_error(settings):
    bad = sdk_client(lambda r: httpx2.Response(200, json=message(text={"diagnosis": 5})))
    with pytest.raises(claude.AdvisorCallError) as info:
        claude.consult(settings, "x", client=bad)
    assert info.value.kind == "invalid_output"


def test_make_client_uses_the_configured_retries_and_registers_the_secret(settings):
    client = claude.make_client(settings)
    assert client.max_retries == settings.advisor_max_retries == 3
    assert "sk-ant-test-0000000000000000" not in redact("key sk-ant-test-0000000000000000 leaked")


def test_log_redaction_covers_anthropic_keys(caplog):
    from app.sources.garmin.redact import install_log_redaction

    install_log_redaction()
    key = "sk-ant-api03-AbCdEfGhIj_KlMnOpQrStUvWxYz0123456789"
    assert key not in redact(f"x-api-key: {key} failed")
    assert key not in redact(f"bad key {key}")
    log = logging.getLogger("tests.redaction")
    log.disabled = False
    with caplog.at_level(logging.INFO, logger="tests.redaction"):
        log.info("calling with %s", key)
    assert key not in caplog.text and "sk-ant-" not in caplog.text
