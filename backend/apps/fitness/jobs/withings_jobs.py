"""Withings sync job shared by the API, the CLI and the scheduler."""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from apps.fitness.analytics import cache as stats_cache
from apps.fitness.db.session import get_session_factory, session_scope
from apps.fitness.settings import Settings, get_settings
from apps.fitness.stats import rebuild_with_config
from apps.fitness.withings.client import WithingsClient
from apps.fitness.withings.errors import NotConfiguredError, NotConnectedError, TokenRevokedError
from apps.fitness.withings.sync import SyncReport, record_state, sync_weights
from apps.fitness.withings.tokens import TokenStore

if TYPE_CHECKING:
    from sqlalchemy.orm import Session, sessionmaker

log = logging.getLogger(__name__)

_lock = threading.Lock()  # one sync at a time, whether API, CLI-in-process or scheduled
REVOKED_MESSAGE = "Withings access was revoked or expired: connect again"


@dataclass
class SyncOutcome:
    status: Literal["synced", "busy", "not_connected"]
    report: SyncReport | None = None


def build_client(settings: Settings, *, transport=None) -> WithingsClient:
    """The configured client (NotConfiguredError when the credentials are not all set)."""
    if not settings.withings_configured or settings.withings_client_secret is None:
        raise NotConfiguredError("Withings is not configured")
    return WithingsClient(
        (settings.withings_client_id or "").strip(),
        settings.withings_client_secret.get_secret_value().strip(),
        (settings.withings_redirect_uri or "").strip(),
        TokenStore(settings.withings_token_cache_path),
        transport=transport,
    )


def after_sync(report: SyncReport) -> None:
    """Post-processing shared by every sync entry point: bodyweight feeds the e1RM of
    bodyweight-loaded lifts, so new weights rebuild the stats."""
    if report.weights_changed:
        stats_cache.invalidate()
        with session_scope() as db:
            n = rebuild_with_config(db)
        log.info("stats rebuilt after new Withings weights (%d rows)", n)


def run_sync(
    settings: Settings | None = None,
    *,
    client: WithingsClient | None = None,
    session_factory: sessionmaker[Session] | None = None,
) -> SyncOutcome:
    """Incremental sync. `busy` if another sync holds the lock, `not_connected` if there is no
    stored token or Withings revoked it (the token file is then deleted and the error recorded
    in sync_state). Other failures raise (`WithingsError`, `RateLimitedError`)."""
    s = settings or get_settings()
    factory = session_factory or get_session_factory()
    if not _lock.acquire(blocking=False):
        log.warning("Withings sync already running; skipping")
        return SyncOutcome("busy")
    owned = client is None
    try:
        wc = client or build_client(s)
        try:
            if not wc.store.exists():
                return SyncOutcome("not_connected")
            try:
                report = sync_weights(factory, wc, s)
            except NotConnectedError as exc:
                if isinstance(exc, TokenRevokedError):
                    wc.store.delete()
                    record_state(factory, error=REVOKED_MESSAGE)
                    log.warning("Withings token revoked; connection removed")
                return SyncOutcome("not_connected")
            after_sync(report)
            return SyncOutcome("synced", report)
        finally:
            if owned:
                wc.close()
    finally:
        _lock.release()
