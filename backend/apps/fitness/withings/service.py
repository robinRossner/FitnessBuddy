"""What the Withings endpoints do (docs/api/sync.md "Withings"): status, the authorize URL, the
OAuth callback, a manual sync and disconnect. Errors are `ServiceError`s carrying the HTTP
status."""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from apps.fitness.db.models import SyncState
from apps.fitness.errors import ServiceError
from apps.fitness.jobs import withings_jobs
from apps.fitness.settings import Settings, get_settings
from apps.fitness.withings import state as oauth_state
from apps.fitness.withings.errors import (
    InvalidStateError,
    NotConfiguredError,
    RateLimitedError,
    WithingsError,
)
from apps.fitness.withings.sync import STATE_KIND, STATE_SOURCE, latest_weight
from apps.fitness.withings.tokens import TokenStore

log = logging.getLogger(__name__)

NOT_CONFIGURED = "Withings is not configured"
DEFAULT_RETRY_AFTER_S = 600


def _utc_s(value: datetime | None) -> datetime | None:
    """Whole seconds, so the JSON has no fractional part (Swift's plain ISO-8601 decoder)."""
    return None if value is None else value.replace(microsecond=0)


def require_configured(s: Settings) -> None:
    if not s.withings_configured:
        raise ServiceError(503, NOT_CONFIGURED)


def _state_key(s: Settings) -> str:
    assert s.withings_client_secret is not None
    return s.withings_client_secret.get_secret_value().strip()


def _latest(db: Session) -> dict[str, Any]:
    db.rollback()  # fresh snapshot: the sync wrote through its own sessions
    latest = latest_weight(db)
    return {
        "latest_measured_at": _utc_s(latest[0]) if latest else None,
        "latest_weight_kg": latest[1] if latest else None,
    }


def status(db: Session, settings: Settings | None = None) -> dict[str, Any]:
    """`GET /withings/status`: works unconfigured (everything false / null)."""
    s = settings or get_settings()
    st = db.scalar(
        select(SyncState).where(SyncState.source == STATE_SOURCE, SyncState.kind == STATE_KIND)
    )
    return {
        "configured": s.withings_configured,
        "connected": s.withings_configured and TokenStore(s.withings_token_cache_path).exists(),
        "last_sync_at": _utc_s(st.last_success_at) if st else None,
        "last_error": st.last_error if st else None,
        **_latest(db),
    }


def authorize_url(client: str, settings: Settings | None = None) -> str:
    """`POST /withings/connect`: the Withings consent page URL for a flow started by `client`
    (`app` | `web`). 503 when not configured."""
    s = settings or get_settings()
    require_configured(s)
    with withings_jobs.build_client(s) as wc:
        return wc.authorize_url(oauth_state.sign_state(_state_key(s), client))


@dataclass(frozen=True)
class CallbackResult:
    client: str  # who started the flow; `web` too when the state was unusable
    ok: bool
    reason: str | None = None  # denied | invalid_state | exchange_failed | not_configured


def handle_callback(
    code: str | None,
    state: str | None,
    error: str | None,
    settings: Settings | None = None,
) -> CallbackResult:
    """`GET /withings/callback`: verify the state, then exchange the code and store the tokens.
    Never raises; the outcome says what to show."""
    s = settings or get_settings()
    if not s.withings_configured:
        return CallbackResult("web", False, "not_configured")
    try:
        client = oauth_state.verify_state(_state_key(s), state or "")
    except InvalidStateError:
        return CallbackResult("web", False, "invalid_state")
    if error or not code:
        return CallbackResult(client, False, "denied")
    try:
        with withings_jobs.build_client(s) as wc:
            wc.exchange_code(code)
    except WithingsError as exc:
        log.warning("Withings code exchange failed: %s", exc)
        return CallbackResult(client, False, "exchange_failed")
    return CallbackResult(client, True)


def sync_now(db: Session, settings: Settings | None = None) -> dict[str, Any]:
    """`POST /withings/sync`: 503 not configured, 502 Withings failed, 429 rate limited."""
    s = settings or get_settings()
    require_configured(s)
    try:
        outcome = withings_jobs.run_sync(s)
    except NotConfiguredError:
        raise ServiceError(503, NOT_CONFIGURED) from None
    except RateLimitedError as exc:
        wait = math.ceil(exc.retry_after) if exc.retry_after else DEFAULT_RETRY_AFTER_S
        raise ServiceError(
            429, "Withings rate limit: try again later", {"Retry-After": str(wait)}
        ) from None
    except WithingsError as exc:
        raise ServiceError(502, str(exc)) from None
    out = {
        "status": outcome.status,
        "new_measurements": outcome.report.new if outcome.report else 0,
        **_latest(db),
    }
    return out


def disconnect(settings: Settings | None = None) -> None:
    """`DELETE /withings/connection`: forget the tokens (idempotent; stored weigh-ins and
    sync_state stay). Works unconfigured too."""
    s = settings or get_settings()
    TokenStore(s.withings_token_cache_path).delete()
