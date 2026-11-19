"""The bearer token, the failed-attempt throttle, the non-ASCII guard and hidden API docs."""

from __future__ import annotations

import pytest
from fastapi import Depends
from fastapi.testclient import TestClient

from apps.fitness.api.deps import get_db
from core.auth import get_throttle, get_trusted_proxies, token_dependency
from core.settings import get_core_settings
from core.throttle import FailureThrottle, client_ip, parse_networks
from server import create_app

FITNESS_TOKEN = "fitness-test-token"
OTHER_TOKEN = "some-other-token"  # any token but the fitness one
FITNESS_URL = "/api/v1/variants"  # a real fitness route (needs the db fixture)


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _settings_env(monkeypatch, **env: str) -> None:
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    for cached in (get_core_settings, get_throttle, get_trusted_proxies):
        cached.cache_clear()


@pytest.fixture
def app(db, monkeypatch):
    """The real app (with the db fixture)."""
    _settings_env(monkeypatch, API_TOKEN=FITNESS_TOKEN)
    app = create_app(docs_enabled=False)
    app.dependency_overrides[get_db] = lambda: db
    return app


@pytest.fixture
def clock(app):
    clock = Clock()
    throttle = FailureThrottle(limit=3, window_s=60, clock=clock)
    app.dependency_overrides[get_throttle] = lambda: throttle
    clock.throttle = throttle
    return clock


def client_at(app, host: str = "203.0.113.9") -> TestClient:
    return TestClient(app, client=(host, 50000))


# --- per-app tokens ----------------------------------------------------------------------


def test_only_the_fitness_token_opens_the_fitness_api(app):
    client = client_at(app)
    assert client.get(FITNESS_URL, headers=bearer(FITNESS_TOKEN)).status_code == 200
    assert client.get(FITNESS_URL, headers=bearer(OTHER_TOKEN)).status_code == 401
    # also under the app-scoped fitness prefix
    assert client.get("/api/v1/fitness/variants", headers=bearer(OTHER_TOKEN)).status_code == 401


def test_unset_fitness_token_is_503(app, monkeypatch):
    monkeypatch.setenv("API_TOKEN", "")
    get_core_settings.cache_clear()
    r = client_at(app).get(FITNESS_URL, headers=bearer(FITNESS_TOKEN))
    assert r.status_code == 503 and "API_TOKEN" in r.json()["detail"]


def test_token_dependency_accepts_only_its_getter_token():
    from fastapi import FastAPI

    app = FastAPI()
    app.get("/x", dependencies=[Depends(token_dependency("X_TOKEN", lambda: "secret-x"))])(
        lambda: "ok"
    )
    client = TestClient(app)
    assert client.get("/x", headers=bearer("secret-x")).status_code == 200
    assert client.get("/x", headers=bearer("other")).status_code == 401
    assert client.get("/x", headers={"Authorization": "Basic secret-x"}).status_code == 401
    assert client.get("/x").status_code == 401


# --- non-ASCII guard ---------------------------------------------------------------------


@pytest.mark.parametrize("raw", [b"Bearer t\xc3\xb6ken", b"Bearer \xff\xfe", b"Bearer "])
def test_non_ascii_authorization_header_is_401_not_500(app, raw):
    client = client_at(app)
    assert client.get(FITNESS_URL, headers={"Authorization": raw}).status_code == 401


# --- throttle ----------------------------------------------------------------------------


def test_throttle_answers_429_with_retry_after_after_the_limit(app, clock):
    client = client_at(app)
    for _ in range(3):  # limit is 3 failures
        assert client.get(FITNESS_URL, headers=bearer("nope")).status_code == 401
    r = client.get(FITNESS_URL, headers=bearer("nope"))
    assert r.status_code == 429
    assert r.headers["Retry-After"] == "60"
    assert client.get(FITNESS_URL).status_code == 429  # a missing token too
    clock.now += 25
    assert client.get(FITNESS_URL).headers["Retry-After"] == "35"
    # another IP is not affected
    assert client_at(app, "203.0.113.77").get(FITNESS_URL).status_code == 401


def test_valid_token_always_succeeds_from_a_throttled_ip(app, clock):
    client = client_at(app)
    for _ in range(5):
        client.get(FITNESS_URL, headers=bearer("nope"))
    assert client.get(FITNESS_URL, headers=bearer("nope")).status_code == 429
    assert client.get(FITNESS_URL, headers=bearer(FITNESS_TOKEN)).status_code == 200
    assert client.get(FITNESS_URL, headers=bearer("nope")).status_code == 429  # still throttled


def test_throttle_window_expires(app, clock):
    client = client_at(app)
    for _ in range(3):
        client.get(FITNESS_URL)
    assert client.get(FITNESS_URL).status_code == 429
    clock.now += 59.9
    assert client.get(FITNESS_URL).status_code == 429
    clock.now += 0.2  # the first failure is now older than the window
    assert client.get(FITNESS_URL).status_code == 401


def test_failures_slide_out_one_by_one(app, clock):
    client = client_at(app)
    for _ in range(3):
        client.get(FITNESS_URL)
        clock.now += 30  # failures at t, t+30, t+60 -> only two left in the window at t+90
    assert client.get(FITNESS_URL).status_code == 401  # t+90: the t failure expired, 2 remain


def test_health_is_never_throttled(app, clock):
    client = client_at(app)
    for _ in range(5):
        client.get(FITNESS_URL)
    assert client.get(FITNESS_URL).status_code == 429
    for url in ("/health", "/api/v1/health", "/api/v1/fitness/health"):
        assert client.get(url).status_code == 200


def test_spoofed_forwarded_for_is_ignored_from_an_untrusted_peer(app, clock):
    client = client_at(app, "203.0.113.9")  # not a trusted proxy
    for i in range(3):
        r = client.get(FITNESS_URL, headers={"X-Forwarded-For": f"198.51.100.{i}"})
        assert r.status_code == 401
    # rotating the header did not help: the peer is what counts
    assert client.get(FITNESS_URL, headers={"X-Forwarded-For": "198.51.100.200"}).status_code == 429


def test_forwarded_for_rightmost_entry_is_used_from_a_trusted_proxy(app, clock):
    proxy = client_at(app, "172.18.0.2")  # default trusted: private (Docker) range
    xff = {"X-Forwarded-For": "6.6.6.6, 198.51.100.4"}  # left part is client-controlled
    for _ in range(3):
        assert proxy.get(FITNESS_URL, headers=xff).status_code == 401
    assert proxy.get(FITNESS_URL, headers=xff).status_code == 429
    # the same real client is throttled whatever it puts on the left ...
    other_left = {"X-Forwarded-For": "7.7.7.7, 198.51.100.4"}
    assert proxy.get(FITNESS_URL, headers=other_left).status_code == 429
    # ... and a different real client behind the proxy is not
    assert proxy.get(FITNESS_URL, headers={"X-Forwarded-For": "198.51.100.5"}).status_code == 401
    # no (or garbage) header from the proxy: attributed to the proxy itself
    assert proxy.get(FITNESS_URL, headers={"X-Forwarded-For": "not-an-ip"}).status_code == 401


def test_trusted_proxies_are_configurable(app, clock, monkeypatch):
    _settings_env(monkeypatch, TRUSTED_PROXY_IPS="203.0.113.9")
    client = client_at(app, "203.0.113.9")
    for _ in range(3):
        client.get(FITNESS_URL, headers={"X-Forwarded-For": "198.51.100.4"})
    assert client.get(FITNESS_URL, headers={"X-Forwarded-For": "198.51.100.4"}).status_code == 429
    assert client.get(FITNESS_URL, headers={"X-Forwarded-For": "198.51.100.5"}).status_code == 401
    # the Docker range is no longer trusted
    docker = client_at(app, "172.18.0.2")
    for i in range(4):
        r = docker.get(FITNESS_URL, headers={"X-Forwarded-For": f"198.51.100.{i}"})
    assert r.status_code == 429


def test_limit_and_window_come_from_settings(monkeypatch):
    _settings_env(monkeypatch, AUTH_MAX_FAILURES="7", AUTH_FAILURE_WINDOW_S="120")
    throttle = get_throttle()
    assert (throttle.limit, throttle.window_s) == (7, 120)


# --- FailureThrottle / client_ip units ---------------------------------------------------


def test_throttle_memory_is_bounded_and_stale_ips_are_evicted():
    clock = Clock()
    t = FailureThrottle(limit=2, window_s=60, clock=clock, max_ips=5)
    for i in range(50):  # many distinct IPs inside one window: hard cap
        t.record_failure(f"10.0.0.{i}")
    assert len(t._failures) <= 6
    clock.now += 61  # all stale: swept by the next write, one fresh entry remains
    t.record_failure("10.9.9.9")
    assert list(t._failures) == ["10.9.9.9"]
    assert t.blocked_for("10.0.0.1") == 0


def test_blocked_ip_stops_recording_so_per_ip_memory_is_bounded():
    clock = Clock()
    t = FailureThrottle(limit=3, window_s=60, clock=clock)
    for _ in range(100):
        t.record_failure("1.1.1.1")
    assert len(t._failures["1.1.1.1"]) == 3
    assert t.blocked_for("1.1.1.1") == 60


class _Req:
    def __init__(self, host, xff=()):
        from types import SimpleNamespace

        self.client = SimpleNamespace(host=host) if host else None
        self.headers = SimpleNamespace(getlist=lambda _name: list(xff))


def test_client_ip_rules():
    nets = parse_networks("172.16.0.0/12, fc00::/7")
    assert client_ip(_Req("8.8.8.8", ["1.2.3.4"]), nets) == "8.8.8.8"
    assert client_ip(_Req("172.20.0.3", ["1.2.3.4"]), nets) == "1.2.3.4"
    assert client_ip(_Req("172.20.0.3", ["9.9.9.9", "1.2.3.4"]), nets) == "1.2.3.4"  # two headers
    assert client_ip(_Req("172.20.0.3", []), nets) == "172.20.0.3"
    assert client_ip(_Req("172.20.0.3", ["x, "]), nets) == "172.20.0.3"
    assert client_ip(_Req("fd00::2", ["2001:db8::1"]), nets) == "2001:db8::1"
    assert client_ip(_Req("testclient", ["1.2.3.4"]), nets) == "testclient"
    assert client_ip(_Req(None), nets) == "unknown"


# --- API docs ----------------------------------------------------------------------------

DOC_URLS = ("/docs", "/redoc", "/openapi.json")


def test_docs_are_hidden_by_default(monkeypatch):
    _settings_env(monkeypatch)
    monkeypatch.delenv("DOCS_ENABLED", raising=False)
    for docs_enabled in (None, False):
        client = TestClient(create_app(docs_enabled))
        assert [client.get(u).status_code for u in DOC_URLS] == [404, 404, 404]


def test_docs_can_be_enabled(monkeypatch):
    client = TestClient(create_app(docs_enabled=True))
    assert [client.get(u).status_code for u in DOC_URLS] == [200, 200, 200]
    _settings_env(monkeypatch, DOCS_ENABLED="true")
    assert TestClient(create_app()).get("/openapi.json").status_code == 200


def test_openapi_schema_is_still_available_in_process():
    assert "/health" in create_app(docs_enabled=False).openapi()["paths"]
