"""Personal API client: token exchange / cache / refresh, backoff, key errors, redaction."""

from __future__ import annotations

import base64
import json
import logging
import stat
from pathlib import Path

import httpx
import pytest

from apps.fitness.garmin.errors import (
    BadRequestError,
    KeyExpiredError,
    NotFoundError,
    RateLimitedError,
    ServerError,
)
from apps.fitness.garmin.personal_client import TOKEN_PATH, PersonalApiClient, jwt_claims
from core.redact import redact

KEY = "test-api-key-0123456789"
BASE = "https://personal.example.test"


def make_jwt(exp: float) -> str:
    def enc(obj: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).decode().rstrip("=")

    return f"{enc({'alg': 'none'})}.{enc({'exp': exp, 'sub': 'x'})}.sig"


class Clock:
    def __init__(self, now: float = 1_000_000.0) -> None:
        self.now = now
        self.sleeps: list[float] = []

    def time(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


class Server:
    """Scripted Garmin: token endpoint + one data endpoint with queued responses."""

    def __init__(self, clock: Clock, token_ttl: float = 86400) -> None:
        self.clock, self.token_ttl = clock, token_ttl
        self.exchanges = 0
        self.queue: list[httpx.Response] = []
        self.auth_seen: list[str] = []
        self.token_status = 200

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == TOKEN_PATH:
            assert request.headers["authorization"] == f"ApiKey {KEY}"
            self.exchanges += 1
            if self.token_status != 200:
                return httpx.Response(self.token_status, text="denied")
            return httpx.Response(200, text=make_jwt(self.clock.now + self.token_ttl))
        self.auth_seen.append(request.headers["authorization"])
        if self.queue:
            return self.queue.pop(0)
        return httpx.Response(200, json={"ok": True})


def client_for(tmp_path: Path, server: Server, clock: Clock, **kw) -> PersonalApiClient:
    return PersonalApiClient(
        KEY,
        base_url=BASE,
        token_cache_path=tmp_path / "var" / "token.json",
        interval_s=kw.pop("interval_s", 0.0),
        transport=httpx.MockTransport(server),
        clock=clock.time,
        sleep=clock.sleep,
        jitter=lambda: 0.5,
        **kw,
    )


def test_token_exchange_cached_in_memory_and_on_disk(tmp_path):
    clock = Clock()
    server = Server(clock)
    c = client_for(tmp_path, server, clock)
    assert c.get("/x") == {"ok": True}
    assert c.get("/x") == {"ok": True}
    assert server.exchanges == 1
    cache = tmp_path / "var" / "token.json"
    assert stat.S_IMODE(cache.stat().st_mode) == 0o600
    # a new client (new process) reuses the cached token
    c2 = client_for(tmp_path, server, clock)
    c2.get("/x")
    assert server.exchanges == 1
    assert server.auth_seen[0] == server.auth_seen[-1]
    assert jwt_claims(c.token().access_token)["exp"] == pytest.approx(clock.now + 86400)


def test_token_refreshed_before_expiry(tmp_path):
    clock = Clock()
    server = Server(clock)
    c = client_for(tmp_path, server, clock)
    c.get("/x")
    clock.now += 86400 - 1800  # < 1 h left -> renew proactively
    c.get("/x")
    assert server.exchanges == 2


def test_cache_for_another_key_is_ignored(tmp_path):
    clock = Clock()
    server = Server(clock)
    client_for(tmp_path, server, clock).get("/x")
    cache = tmp_path / "var" / "token.json"
    data = json.loads(cache.read_text())
    data["fingerprint"] = "other"
    cache.write_text(json.dumps(data))
    client_for(tmp_path, server, clock).get("/x")
    assert server.exchanges == 2


def test_401_on_data_call_forces_one_refresh(tmp_path):
    clock = Clock()
    server = Server(clock)
    server.queue = [httpx.Response(401)]
    c = client_for(tmp_path, server, clock)
    assert c.get("/x") == {"ok": True}
    assert server.exchanges == 2


def test_expired_key_gives_clear_error(tmp_path):
    clock = Clock()
    server = Server(clock)
    server.token_status = 401
    c = client_for(tmp_path, server, clock)
    with pytest.raises(KeyExpiredError) as err:
        c.get("/x")
    assert "personalApiToken" in str(err.value)
    assert KEY not in str(err.value)


def test_key_revoked_mid_session(tmp_path):
    clock = Clock()
    server = Server(clock)
    server.queue = [httpx.Response(401)]
    c = client_for(tmp_path, server, clock)
    c.token()
    server.token_status = 403
    with pytest.raises(KeyExpiredError):
        c.get("/x")


def test_429_backoff_respects_retry_after(tmp_path):
    clock = Clock()
    server = Server(clock)
    server.queue = [
        httpx.Response(429, headers={"Retry-After": "7"}),
        httpx.Response(429, headers={"Retry-After": "3"}),
        httpx.Response(200, json=[1, 2]),
    ]
    c = client_for(tmp_path, server, clock)
    assert c.get("/x") == [1, 2]
    assert clock.sleeps == [7.0, 3.0]


def test_5xx_exponential_backoff_with_jitter_then_success(tmp_path):
    clock = Clock()
    server = Server(clock)
    server.queue = [httpx.Response(503), httpx.Response(502), httpx.Response(200, json={"a": 1})]
    c = client_for(tmp_path, server, clock)
    assert c.get("/x") == {"a": 1}
    assert clock.sleeps == [2.0 * 1.0, 4.0 * 1.0]  # base * 2**n * (0.5 + jitter 0.5)


def test_429_gives_up_after_retries(tmp_path):
    clock = Clock()
    server = Server(clock)
    server.queue = [httpx.Response(429)] * 10
    c = client_for(tmp_path, server, clock, max_retries=2)
    with pytest.raises(RateLimitedError):
        c.get("/x")
    assert len(clock.sleeps) == 2


def test_huge_retry_after_aborts_instead_of_sleeping(tmp_path):
    clock = Clock()
    server = Server(clock)
    server.queue = [httpx.Response(429, headers={"Retry-After": "7200"})]
    c = client_for(tmp_path, server, clock)
    with pytest.raises(RateLimitedError) as err:
        c.get("/x")
    assert err.value.retry_after == 7200
    assert clock.sleeps == []


def test_persistent_5xx_raises_server_error(tmp_path):
    clock = Clock()
    server = Server(clock)
    server.queue = [httpx.Response(500)] * 10
    with pytest.raises(ServerError):
        client_for(tmp_path, server, clock, max_retries=1).get("/x")


def test_network_error_retried(tmp_path):
    clock = Clock()
    server = Server(clock)
    calls = {"n": 0}

    def flaky(request: httpx.Request) -> httpx.Response:
        if request.url.path != TOKEN_PATH:
            calls["n"] += 1
            if calls["n"] == 1:
                raise httpx.ConnectError("boom")
        return server(request)

    c = PersonalApiClient(
        KEY,
        base_url=BASE,
        token_cache_path=None,
        interval_s=0,
        transport=httpx.MockTransport(flaky),
        clock=clock.time,
        sleep=clock.sleep,
        jitter=lambda: 0.5,
    )
    assert c.get("/x") == {"ok": True}


def test_status_mapping_and_empty_bodies(tmp_path):
    clock = Clock()
    server = Server(clock)
    server.queue = [httpx.Response(400), httpx.Response(404), httpx.Response(204)]
    c = client_for(tmp_path, server, clock)
    with pytest.raises(BadRequestError):
        c.get("/x")
    with pytest.raises(NotFoundError):
        c.get("/x")
    assert c.get("/x") is None


def test_pacing_between_requests(tmp_path):
    clock = Clock()
    server = Server(clock)
    c = client_for(tmp_path, server, clock, interval_s=1.0)
    c.get("/a")
    c.get("/b")
    assert sum(clock.sleeps) >= 1.0


def test_secrets_never_leak_into_logs_or_repr(tmp_path, caplog):
    clock = Clock()
    server = Server(clock)
    c = client_for(tmp_path, server, clock)
    server.queue = [httpx.Response(503)]
    with caplog.at_level(logging.DEBUG):
        c.get("/x")
        logging.getLogger("t").warning("Authorization: Bearer %s and key %s", make_jwt(1), KEY)
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert KEY not in text
    assert "eyJ" not in text
    assert KEY not in repr(c)
    assert redact("Authorization: ApiKey abcdefghijkl") == "Authorization: ***"
