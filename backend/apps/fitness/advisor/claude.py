"""The one place that talks to Claude: the Messages API here (official `anthropic` SDK,
non-streaming) or, with a subscription token, headless Claude Code (providers/claude_code.py).
Provider choice: ADVISOR_PROVIDER=api|claude_code|auto (settings.advisor_provider_resolved);
`provider_status(settings)` is the dict for the coach settings response.

Request shape (Claude Opus 5.5; see the SDK docs):
- `thinking: {type: "adaptive"}` and `output_config.effort: "high"` (the default would be medium)
- strict JSON through structured outputs: `messages.parse(output_format=AdvisorOutput)`; no
  prefill and no forced `tool_choice` (both are 400s on this model)
- a frozen system prompt with the cache breakpoint on its last block; all volatile data is in the
  user message after it
- server-side refusal fallbacks: `fallbacks="default"` + beta `server-side-fallback-2026-07-01`,
  which lives on `client.beta.messages` (hence `.beta.messages.parse`)
- `stop_reason` is checked before the content is read ("refusal", "max_tokens")
- errors: RateLimitError -> APIStatusError -> APIConnectionError (the SDK has already retried
  429 / 5xx / connection errors with exponential backoff, `max_retries`), each turned into an
  `AdvisorCallError` whose message never contains a key or a header.
"""

from __future__ import annotations

import logging
from typing import Any

import anthropic
from pydantic import ValidationError

from apps.fitness.advisor.prompt import SYSTEM_PROMPT
from apps.fitness.advisor.providers import provider_status as provider_status
from apps.fitness.advisor.providers.base import AdvisorCallError, ClaudeResult
from apps.fitness.advisor.schema import AdvisorOutput
from apps.fitness.settings import Settings
from apps.fitness.sources.garmin.redact import install_log_redaction, redact, register_secret

log = logging.getLogger(__name__)

FALLBACK_BETA = "server-side-fallback-2026-07-01"


def make_client(settings: Settings) -> anthropic.Anthropic:
    """Client with the SDK's own retries / backoff. Raises AdvisorCallError without a key."""
    key = settings.advisor_api_key
    if key is None:
        raise AdvisorCallError("no_key", "ANTHROPIC_API_KEY is not configured")
    install_log_redaction()
    register_secret(key)
    return anthropic.Anthropic(
        api_key=key, max_retries=settings.advisor_max_retries, timeout=settings.advisor_timeout_s
    )


def request_kwargs(settings: Settings, user_text: str) -> dict[str, Any]:
    """Everything `messages.parse` is called with (also what the tests assert on)."""
    return {
        "model": settings.advisor_model,
        "max_tokens": settings.advisor_max_tokens,
        "thinking": {"type": "adaptive"},
        "output_config": {"effort": "high"},
        "system": [{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
        "messages": [{"role": "user", "content": user_text}],
        "output_format": AdvisorOutput,
        "betas": [FALLBACK_BETA],
        "fallbacks": "default",
    }


def _usage(resp: Any) -> dict[str, int]:
    u = getattr(resp, "usage", None)
    return {
        "input_tokens": int(getattr(u, "input_tokens", 0) or 0),
        "output_tokens": int(getattr(u, "output_tokens", 0) or 0),
        "cache_read_input_tokens": int(getattr(u, "cache_read_input_tokens", 0) or 0),
        "cache_creation_input_tokens": int(getattr(u, "cache_creation_input_tokens", 0) or 0),
    }


def _fell_back(resp: Any) -> bool:
    if any(getattr(b, "type", None) == "fallback" for b in getattr(resp, "content", None) or []):
        return True
    iterations = getattr(getattr(resp, "usage", None), "iterations", None) or []
    return any(getattr(i, "type", None) == "fallback_message" for i in iterations)


def _stop_details(resp: Any) -> dict[str, Any] | None:
    d = getattr(resp, "stop_details", None)
    if d is None:
        return None
    return {
        "category": getattr(d, "category", None),
        "explanation": getattr(d, "explanation", None),
    }


def consult(settings: Settings, user_text: str, *, client: Any | None = None) -> ClaudeResult:
    """One non-streaming call. Never returns content of a refused / truncated response.

    The provider is `settings.advisor_provider_resolved`: `claude_code` runs headless Claude Code
    on the subscription token (providers/claude_code.py); otherwise the Messages API below. An
    explicit `client` (tests) always means the API path.
    """
    if client is None and settings.advisor_provider_resolved == "claude_code":
        from apps.fitness.advisor.providers.claude_code import ClaudeCodeProvider

        return ClaudeCodeProvider(settings).consult(user_text)
    client = client or make_client(settings)
    try:
        resp = client.beta.messages.parse(**request_kwargs(settings, user_text))
    except anthropic.RateLimitError as exc:
        raise AdvisorCallError(
            "rate_limit",
            redact(f"Claude rate limit reached (HTTP 429): {exc.message}"),
            429,
        ) from None
    except anthropic.APIStatusError as exc:
        raise AdvisorCallError(
            "api_status",
            redact(f"Claude API error (HTTP {exc.status_code}): {exc.message}"),
            exc.status_code,
        ) from None
    except anthropic.APIConnectionError as exc:
        raise AdvisorCallError(
            "connection", redact(f"Could not reach the Claude API: {exc.message}")
        ) from None
    except (ValidationError, ValueError) as exc:
        raise AdvisorCallError(
            "invalid_output", redact(f"Claude's answer did not match the schema: {exc}")[:500]
        ) from None

    result = ClaudeResult(
        outcome="ok",
        stop_reason=getattr(resp, "stop_reason", None),
        stop_details=_stop_details(resp),
        usage=_usage(resp),
        request_id=getattr(resp, "_request_id", None),
        model=getattr(resp, "model", None),
        fell_back=_fell_back(resp),
    )
    # stop_reason first: a refused or truncated response has no usable content
    if result.stop_reason == "refusal":
        result.outcome = "refusal"
    elif result.stop_reason == "max_tokens":
        result.outcome = "max_tokens"
    elif result.stop_reason not in ("end_turn", "stop_sequence", None):
        result.outcome = "unexpected_stop"
    else:
        result.output = getattr(resp, "parsed_output", None)
        if result.output is None:
            result.outcome = "empty"
    log.info(
        "advisor call: %s stop=%s in=%d out=%d cache_read=%d cache_write=%d request=%s",
        result.outcome,
        result.stop_reason,
        result.usage["input_tokens"],
        result.usage["output_tokens"],
        result.usage["cache_read_input_tokens"],
        result.usage["cache_creation_input_tokens"],
        result.request_id,
    )
    return result
