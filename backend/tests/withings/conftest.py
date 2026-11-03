"""Withings tests: client, OAuth state, sync, jobs, API, scheduler. Every token file lives in
tmp_path; HTTP is `httpx.MockTransport` (tests/withings/support.py)."""

from __future__ import annotations

from pathlib import Path

import pytest

from apps.fitness.settings import Settings
from apps.fitness.withings.client import WithingsClient
from apps.fitness.withings.tokens import Tokens, TokenStore
from tests.withings.support import CLIENT_ID, REDIRECT, SECRET, Clock, FakeWithings


@pytest.fixture(autouse=True)
def _isolation(auth_throttle_off, clean_settings):
    """See tests/conftest.py: bad bearer tokens through the app; fresh settings caches."""


@pytest.fixture
def token_path(tmp_path: Path) -> Path:
    return tmp_path / "var" / "withings_token.json"


@pytest.fixture
def settings(token_path: Path) -> Settings:
    """Configured Settings that ignore any real .env / environment."""
    return Settings(
        _env_file=None,
        withings_client_id=CLIENT_ID,
        withings_client_secret=SECRET,  # type: ignore[arg-type]
        withings_redirect_uri=REDIRECT,
        withings_token_cache_path=token_path,
        user_tz="Europe/Vienna",
    )


@pytest.fixture
def unconfigured(token_path: Path) -> Settings:
    return Settings(_env_file=None, withings_token_cache_path=token_path)


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def fake(clock: Clock) -> FakeWithings:
    return FakeWithings(clock)


@pytest.fixture
def store(token_path: Path) -> TokenStore:
    return TokenStore(token_path)


@pytest.fixture
def connected(store: TokenStore, clock: Clock) -> Tokens:
    """A stored token pair, valid for another hour."""
    tokens = Tokens(
        "4711", "access-0-abcdefgh", "refresh-0-abcdefgh", clock.now + 3600, "user.metrics"
    )
    store.save(tokens)
    return tokens


@pytest.fixture
def wclient(fake: FakeWithings, store: TokenStore, clock: Clock):
    c = WithingsClient(
        CLIENT_ID, SECRET, REDIRECT, store, transport=fake.transport(), clock=clock.time
    )
    yield c
    c.close()


@pytest.fixture
def rebuilds(monkeypatch) -> list[int]:
    """Replace the stats rebuild after new weights by a recorder."""
    from apps.fitness.jobs import withings_jobs

    calls: list[int] = []
    monkeypatch.setattr(withings_jobs, "rebuild_with_config", lambda db: calls.append(1) or 7)
    return calls
