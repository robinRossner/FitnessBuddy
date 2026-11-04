"""Withings endpoints on both prefixes: status, connect, callback, sync, disconnect."""

from __future__ import annotations

from datetime import UTC, datetime
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastapi.testclient import TestClient

from apps.fitness.api.deps import get_db, get_settings_dep
from apps.fitness.jobs import withings_jobs
from apps.fitness.settings import Settings
from apps.fitness.withings import state as oauth_state
from apps.fitness.withings.client import WithingsClient
from apps.fitness.withings.tokens import TokenStore
from core.auth import get_api_token
from server import app
from tests.support.world import AUTH, TOKEN
from tests.withings.support import CLIENT_ID, REDIRECT, SECRET, fail, grp

PREFIXES = ("/api/v1", "/api/v1/fitness")
TS = int(datetime(2026, 11, 2, 5, 30, tzinfo=UTC).timestamp())


@pytest.fixture
def make_client(session_factory, pin_session_factory, fake, clock, monkeypatch, rebuilds):
    """TestClient factory for given Settings; Withings HTTP goes to `fake`."""

    def build(settings: Settings, *, headers=AUTH) -> TestClient:
        def _db():
            session = session_factory()
            try:
                yield session
            finally:
                session.close()

        monkeypatch.setattr(
            withings_jobs,
            "build_client",
            lambda s, **kw: WithingsClient(
                CLIENT_ID,
                SECRET,
                REDIRECT,
                TokenStore(s.withings_token_cache_path),
                transport=fake.transport(),
                clock=clock.time,
            ),
        )
        app.dependency_overrides[get_db] = _db
        app.dependency_overrides[get_api_token] = lambda: TOKEN
        app.dependency_overrides[get_settings_dep] = lambda: settings
        return TestClient(app, headers=headers)

    return build


@pytest.fixture
def api(make_client, settings) -> TestClient:
    return make_client(settings)


@pytest.fixture
def api_unconfigured(make_client, unconfigured) -> TestClient:
    return make_client(unconfigured)


def state_for(settings: Settings, client: str) -> str:
    return oauth_state.sign_state(SECRET, client)


# --------------------------------------------------------------------------- status


@pytest.mark.parametrize("prefix", PREFIXES)
def test_status_unconfigured_works_and_is_all_empty(api_unconfigured, prefix):
    r = api_unconfigured.get(f"{prefix}/withings/status")
    assert r.status_code == 200
    assert r.json() == {
        "configured": False,
        "connected": False,
        "last_sync_at": None,
        "last_error": None,
        "latest_measured_at": None,
        "latest_weight_kg": None,
    }


def test_status_configured_connected_after_a_sync(api, fake, connected):
    r = api.get("/api/v1/withings/status").json()
    assert (r["configured"], r["connected"], r["last_sync_at"], r["latest_weight_kg"]) == (
        True,
        True,
        None,
        None,
    )
    fake.groups = [grp(1, TS, 81.25)]
    assert api.post("/api/v1/withings/sync").status_code == 200
    r = api.get("/api/v1/withings/status").json()
    assert r["latest_weight_kg"] == 81.25
    assert r["latest_measured_at"] == "2026-11-02T05:30:00Z"
    assert r["last_sync_at"].endswith("Z") and "." not in r["last_sync_at"]
    assert r["last_error"] is None


def test_status_connected_requires_configuration(api_unconfigured, connected):
    r = api_unconfigured.get("/api/v1/withings/status").json()
    assert r["configured"] is False and r["connected"] is False


# --------------------------------------------------------------------------- connect


def test_connect_503_when_unconfigured(api_unconfigured):
    r = api_unconfigured.post("/api/v1/withings/connect", json={"client": "app"})
    assert r.status_code == 503 and r.json()["detail"] == "Withings is not configured"


@pytest.mark.parametrize("prefix", PREFIXES)
@pytest.mark.parametrize(("body", "who"), [(None, "app"), ({"client": "web"}, "web")])
def test_connect_returns_the_authorize_url(api, settings, prefix, body, who):
    r = api.post(f"{prefix}/withings/connect", json=body)
    assert r.status_code == 200
    url = urlsplit(r.json()["authorize_url"])
    assert f"{url.scheme}://{url.netloc}{url.path}" == (
        "https://account.withings.com/oauth2_user/authorize2"
    )
    q = {k: v[0] for k, v in parse_qs(url.query).items()}
    assert (q["response_type"], q["client_id"], q["scope"], q["redirect_uri"]) == (
        "code",
        CLIENT_ID,
        "user.metrics",
        REDIRECT,
    )
    assert oauth_state.verify_state(SECRET, q["state"]) == who


def test_connect_rejects_an_unknown_client(api):
    assert api.post("/api/v1/withings/connect", json={"client": "tv"}).status_code == 422


# --------------------------------------------------------------------------- callback


@pytest.mark.parametrize("prefix", PREFIXES)
def test_callback_app_redirects_into_the_app_and_stores_tokens(api, fake, store, prefix):
    anon = TestClient(app)  # no bearer: the callback is public
    r = anon.get(
        f"{prefix}/withings/callback",
        params={"code": "auth-code-123456", "state": state_for(None, "app")},
        follow_redirects=False,
    )
    assert r.status_code == 302
    assert r.headers["location"] == "fitnessbuddy://withings?result=connected"
    assert fake.token_requests[0]["code"] == "auth-code-123456"
    assert store.load() is not None


def test_callback_web_shows_a_page(api, store):
    r = api.get(
        "/api/v1/withings/callback",
        params={"code": "auth-code-123456", "state": state_for(None, "web")},
        follow_redirects=False,
    )
    assert r.status_code == 200 and "text/html" in r.headers["content-type"]
    assert "Withings connected" in r.text and "no-store" in r.headers["cache-control"]
    assert store.load() is not None


def test_callback_bad_state_is_a_page_and_never_exchanges(api, fake, store):
    for state in ("", "garbage", state_for(None, "app")[:-3] + "xyz"):
        r = api.get(
            "/api/v1/withings/callback",
            params={"code": "auth-code-123456", "state": state},
            follow_redirects=False,
        )
        assert r.status_code == 400 and "text/html" in r.headers["content-type"]
        assert "Withings connection failed" in r.text
    assert api.get("/api/v1/withings/callback?code=x", follow_redirects=False).status_code == 400
    assert fake.token_requests == [] and store.load() is None


def test_callback_expired_state_is_rejected(api, fake):
    old = oauth_state.sign_state(SECRET, "app", now=datetime.now(UTC).timestamp() - 3600)
    r = api.get(
        "/api/v1/withings/callback",
        params={"code": "auth-code-123456", "state": old},
        follow_redirects=False,
    )
    assert r.status_code == 400 and fake.token_requests == []


def test_callback_denied_in_the_app(api, fake, store):
    r = api.get(
        "/api/v1/withings/callback",
        params={"error": "access_denied", "state": state_for(None, "app")},
        follow_redirects=False,
    )
    assert r.status_code == 302
    assert r.headers["location"] == "fitnessbuddy://withings?result=error&reason=denied"
    assert fake.token_requests == [] and store.load() is None


def test_callback_exchange_failure_in_the_app(api, fake, store):
    fake.token_replies.append(fail(293, "invalid code"))
    r = api.get(
        "/api/v1/withings/callback",
        params={"code": "auth-code-123456", "state": state_for(None, "app")},
        follow_redirects=False,
    )
    assert r.headers["location"] == "fitnessbuddy://withings?result=error&reason=exchange_failed"
    assert store.load() is None


def test_callback_exchange_failure_on_the_web(api, fake):
    fake.token_replies.append(httpx.Response(500))
    r = api.get(
        "/api/v1/withings/callback",
        params={"code": "auth-code-123456", "state": state_for(None, "web")},
        follow_redirects=False,
    )
    assert r.status_code == 400 and "did not accept" in r.text


def test_callback_unconfigured(api_unconfigured):
    r = api_unconfigured.get(
        "/api/v1/withings/callback", params={"code": "c", "state": "s"}, follow_redirects=False
    )
    assert r.status_code == 503 and "not configured" in r.text


@pytest.mark.parametrize("prefix", PREFIXES)
def test_callback_answers_the_dashboard_url_check(api_unconfigured, fake, prefix):
    """The developer dashboard's "Test" probes the bare URL: 200, configured or not."""
    anon = TestClient(app)
    assert anon.head(f"{prefix}/withings/callback").status_code == 200
    r = anon.get(f"{prefix}/withings/callback", follow_redirects=False)
    assert r.status_code == 200 and "text/html" in r.headers["content-type"]
    assert fake.token_requests == []


def test_callback_honours_the_configured_app_url(make_client, settings, fake):
    custom = settings.model_copy(update={"withings_app_redirect_url": "myapp://wb?x=1"})
    r = make_client(custom).get(
        "/api/v1/withings/callback",
        params={"code": "auth-code-123456", "state": state_for(None, "app")},
        follow_redirects=False,
    )
    assert r.headers["location"] == "myapp://wb?x=1&result=connected"


# --------------------------------------------------------------------------- sync


def test_sync_503_when_unconfigured(api_unconfigured):
    r = api_unconfigured.post("/api/v1/withings/sync")
    assert r.status_code == 503 and r.json()["detail"] == "Withings is not configured"


def test_sync_not_connected(api):
    r = api.post("/api/v1/withings/sync")
    assert r.status_code == 200
    assert r.json() == {
        "status": "not_connected",
        "new_measurements": 0,
        "latest_measured_at": None,
        "latest_weight_kg": None,
    }


@pytest.mark.parametrize("prefix", PREFIXES)
def test_sync_imports_and_reports(api, fake, connected, rebuilds, prefix):
    fake.groups = [grp(1, TS, 81.0), grp(2, TS + 86400, 80.5)]
    r = api.post(f"{prefix}/withings/sync")
    assert r.status_code == 200
    assert r.json() == {
        "status": "synced",
        "new_measurements": 2,
        "latest_measured_at": "2026-11-03T05:30:00Z",
        "latest_weight_kg": 80.5,
    }
    assert rebuilds  # new weights rebuild the stats


def test_sync_revoked_deletes_the_token_and_reports_not_connected(api, fake, store, clock):
    from apps.fitness.withings.tokens import Tokens

    store.save(Tokens("4711", "old-access-12345", "refresh-0-abcdefgh", clock.now + 5, "s"))
    fake.token_replies.append(fail(293, "invalid_grant"))
    assert api.post("/api/v1/withings/sync").json()["status"] == "not_connected"
    assert store.load() is None
    status = api.get("/api/v1/withings/status").json()
    assert status["connected"] is False and "connect again" in status["last_error"]


def test_sync_busy(api, connected):
    assert withings_jobs._lock.acquire(blocking=False)
    try:
        r = api.post("/api/v1/withings/sync")
    finally:
        withings_jobs._lock.release()
    assert r.json()["status"] == "busy" and r.json()["new_measurements"] == 0


def test_sync_502_on_a_withings_error_without_secrets(api, fake, connected):
    fake.measure_replies.append(fail(2555, "boom"))
    r = api.post("/api/v1/withings/sync")
    assert r.status_code == 502
    assert "2555" in r.json()["detail"] and "access-0" not in r.text
    assert "2555" in api.get("/api/v1/withings/status").json()["last_error"]


def test_sync_429_with_retry_after(api, fake, connected):
    fake.measure_replies.append(httpx.Response(429, headers={"Retry-After": "120"}))
    r = api.post("/api/v1/withings/sync")
    assert r.status_code == 429 and r.headers["retry-after"] == "120"
    fake.measure_replies.append(fail(601))
    r = api.post("/api/v1/withings/sync")
    assert r.status_code == 429 and int(r.headers["retry-after"]) > 0


# --------------------------------------------------------------------------- disconnect


@pytest.mark.parametrize("prefix", PREFIXES)
def test_disconnect_is_idempotent(api, connected, store, prefix):
    assert store.load() is not None
    for _ in range(2):
        r = api.delete(f"{prefix}/withings/connection")
        assert r.status_code == 204 and r.content == b""
    assert store.load() is None
    assert api.get(f"{prefix}/withings/status").json()["connected"] is False


def test_disconnect_works_unconfigured(api_unconfigured):
    assert api_unconfigured.delete("/api/v1/withings/connection").status_code == 204
