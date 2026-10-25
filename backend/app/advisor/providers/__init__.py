"""Advisor providers: how a consultation reaches Claude.

- `api`: the Messages API with ANTHROPIC_API_KEY (app/advisor/claude.py), paid per token.
- `claude_code`: headless `claude -p` billed to a Claude Pro/Max subscription through the
  long-lived CLAUDE_CODE_OAUTH_TOKEN of `claude setup-token` (providers/claude_code.py).

Which one runs is `Settings.advisor_provider_resolved` (ADVISOR_PROVIDER=api|claude_code|auto).
"""

from __future__ import annotations

from typing import Any

from app.settings import Settings


def provider_status(settings: Settings) -> dict[str, Any]:
    """What the coach settings can show: requested + resolved provider, billing, model.

    Never contains a credential.
    """
    resolved = settings.advisor_provider_resolved
    return {
        "provider": resolved,
        "requested_provider": settings.advisor_provider,
        "credential_configured": resolved != "none",
        "billing": {"api": "api", "claude_code": "subscription"}.get(resolved),
        "model": settings.advisor_model,
    }
