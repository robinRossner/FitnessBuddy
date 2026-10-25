"""Types shared by the advisor providers (Messages API and Claude Code CLI)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.advisor.schema import AdvisorOutput


class AdvisorCallError(Exception):
    """A failed call.

    `kind`: rate_limit | api_status | connection | invalid_output | no_key (API provider) and
    auth | usage_limit | timeout | cli_missing | cli_error (Claude Code provider).
    `auth` means "no / expired subscription login": the consultation should wait as
    `awaiting_key`; `usage_limit` means "retry in the next daily run".
    """

    def __init__(self, kind: str, message: str, status_code: int | None = None):
        super().__init__(message)
        self.kind = kind
        self.message = message
        self.status_code = status_code


@dataclass
class ClaudeResult:
    outcome: str  # ok | refusal | max_tokens | empty | unexpected_stop
    output: AdvisorOutput | None = None
    stop_reason: str | None = None
    stop_details: dict[str, Any] | None = None
    usage: dict[str, Any] = field(default_factory=dict)
    request_id: str | None = None
    model: str | None = None
    fell_back: bool = False
