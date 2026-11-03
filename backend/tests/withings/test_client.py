"""Withings client: OAuth tokens, rotation, status mapping, pagination, unit scaling."""

from __future__ import annotations

import json
import stat
from datetime import UTC, datetime
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from apps.fitness.withings.client import AUTHORIZE_URL, WithingsClient
from apps.fitness.withings.errors import (
    NotConnectedError,
    RateLimitedError,
    TokenRevokedError,
    WithingsError,
)
from apps.fitness.withings.tokens import Tokens
from tests.withings.support import CLIENT_ID, REDIRECT, SECRET, fail, grp

TS = 1_799_000_000


def test_authorize_url(wclient):
    url = urlsplit(wclient.authorize_url("STATE.1"))
    assert f"{url.scheme}://{url.netloc}{url.path}" == AUTHORIZE_URL
    q = {k: v[0] for k, v in parse_qs(url.query).items()}
    assert q == {
        "response_type": "code",
        "client_id": CLIENT_ID,
        "scope": "user.metrics",
        "redirect_uri": REDIRECT,
        "state": "STATE.1",
    }


def test_exchange_code_saves_the_tokens_privately(wclient, fake, store, clock, token_path):
    tokens = wclient.exchange_code("the-auth-code-123")
    req = fake.token_requests[0]
    assert req == {
        "action": "requesttoken",
        "grant_type": "authorization_code",
        "client_id": CLIENT_ID,
        "client_secret": SECRET,
        "code": "the-auth-code-123",
        "redirect_uri": REDIRECT,
    }
    assert tokens.userid == "4711" and tokens.expires_at == clock.now + 10800
    saved = json.loads(token_path.read_text())
    assert saved["refresh_token"] == "refresh-0-abcdefgh" and saved["scope"] == "user.metrics"
    assert stat.S_IMODE(token_path.stat().st_mode) == 0o600
    assert store.load() == tokens
    assert not list(token_path.parent.glob(".*.tmp"))  # atomic write left nothing behind


def test_failed_exchange_leaks_nothing_and_stores_nothing(wclient, fake, store):
    fake.token_replies.append(fail(293, "invalid code the-auth-code-123"))
    with pytest.raises(WithingsError) as exc:
        wclient.exchange_code("the-auth-code-123")
    assert "the-auth-code-123" not in str(exc.value) and SECRET not in str(exc.value)
    assert "293" in str(exc.value)
    assert store.load() is None


def test_no_token_means_not_connected(wclient):
    with pytest.raises(NotConnectedError):
        wclient.weights(0)


def test_fresh_token_is_used_without_refreshing(wclient, fake, connected):
    wclient.weights(0)
    assert fake.token_requests == []
    assert fake.auth_headers == ["Bearer access-0-abcdefgh"]


def test_token_close_to_expiry_is_refreshed_and_the_rotation_persisted_first(
    wclient, fake, store, clock, connected
):
    store.save(Tokens("4711", "old-access-12345", "refresh-0-abcdefgh", clock.now + 200, "s"))
    fake.store = store
    fake.expect_disk_refresh = "refresh-1-abcdefgh"  # the measure call sees the rotated token
    wclient.weights(0)
    req = fake.token_requests[0]
    assert req["grant_type"] == "refresh_token" and req["refresh_token"] == "refresh-0-abcdefgh"
    assert "code" not in req
    assert fake.auth_headers == ["Bearer access-1-abcdefgh"]
    on_disk = store.load()
    assert on_disk is not None
    assert (on_disk.access_token, on_disk.refresh_token) == (
        "access-1-abcdefgh",
        "refresh-1-abcdefgh",
    )
    assert on_disk.expires_at == clock.now + 10800


def test_rejected_access_token_is_refreshed_once_and_retried(wclient, fake, store, connected):
    fake.measure_replies.append(fail(401, "invalid_token"))
    fake.groups = [grp(1, TS, 80.0)]
    got = wclient.weights(0)
    assert [g.weight_kg for g in got] == [80.0]
    assert fake.refreshes == 1 and len(fake.measure_requests) == 2
    assert fake.auth_headers == ["Bearer access-0-abcdefgh", "Bearer access-1-abcdefgh"]
    loaded = store.load()
    assert loaded is not None and loaded.refresh_token == "refresh-1-abcdefgh"


def test_still_rejected_after_a_refresh_is_an_error_not_a_revocation(wclient, fake, connected):
    fake.measure_replies += [fail(401), fail(401)]
    with pytest.raises(WithingsError) as exc:
        wclient.weights(0)
    assert not isinstance(exc.value, NotConnectedError)
    assert fake.refreshes == 1


@pytest.mark.parametrize("status", [293, 401, 503])
def test_refresh_refused_means_revoked(wclient, fake, store, clock, connected, status):
    store.save(Tokens("4711", "old-access-12345", "refresh-0-abcdefgh", clock.now + 10, "s"))
    fake.token_replies.append(fail(status, "invalid_grant"))
    with pytest.raises(TokenRevokedError):
        wclient.weights(0)
    assert fake.measure_requests == []
    assert store.load() is not None  # the client never deletes; the job decides


def test_transient_refresh_failure_keeps_the_connection(wclient, fake, store, clock, connected):
    store.save(Tokens("4711", "old-access-12345", "refresh-0-abcdefgh", clock.now + 10, "s"))
    fake.token_replies.append(fail(2555, "unknown error"))
    with pytest.raises(WithingsError) as exc:
        wclient.weights(0)
    assert not isinstance(exc.value, NotConnectedError)


def test_rate_limit_status_601(wclient, fake, connected):
    fake.measure_replies.append(fail(601, "too many requests"))
    with pytest.raises(RateLimitedError):
        wclient.weights(0)


def test_rate_limit_http_429_carries_retry_after(wclient, fake, connected):
    fake.measure_replies.append(httpx.Response(429, headers={"Retry-After": "90"}))
    with pytest.raises(RateLimitedError) as exc:
        wclient.weights(0)
    assert exc.value.retry_after == 90


def test_other_failures_are_plain_errors_without_secrets(wclient, fake, connected):
    fake.measure_replies.append(fail(2555, "boom Bearer access-0-abcdefgh"))
    with pytest.raises(WithingsError) as exc:
        wclient.weights(0)
    assert "2555" in str(exc.value) and "access-0-abcdefgh" not in str(exc.value)
    fake.measure_replies.append(httpx.Response(503, text="down"))
    with pytest.raises(WithingsError, match="HTTP 503"):
        wclient.weights(0)
    fake.measure_replies.append(httpx.Response(200, text="<html>not json</html>"))
    with pytest.raises(WithingsError, match="unreadable"):
        wclient.weights(0)


def test_network_error_is_a_plain_error(store, connected, clock):
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route")

    c = WithingsClient(
        CLIENT_ID, SECRET, REDIRECT, store, transport=httpx.MockTransport(boom), clock=clock.time
    )
    with pytest.raises(WithingsError, match="ConnectError"):
        c.weights(0)


def test_getmeas_request_shape(wclient, fake, connected):
    wclient.weights(1234)
    assert fake.measure_requests == [
        {"action": "getmeas", "meastype": "1", "category": "1", "lastupdate": "1234"}
    ]


def test_pagination_follows_more_and_offset(wclient, fake, connected):
    fake.groups = [grp(i, TS + i, 70 + i) for i in range(1, 6)]
    fake.page_size = 2
    got = wclient.weights(0)
    assert [g.grpid for g in got] == [1, 2, 3, 4, 5]
    assert [r.get("offset") for r in fake.measure_requests] == [None, "2", "4"]


def test_unit_scaling_and_filtering(wclient, fake, connected):
    fake.groups = [
        grp(1, TS, 72.5),
        {**grp(2, TS + 1, 0), "measures": [{"value": 7251, "type": 1, "unit": -2}]},
        {**grp(3, TS + 2, 0), "measures": [{"value": 725, "type": 1, "unit": -1}]},
        {**grp(4, TS + 3, 0), "measures": [{"value": 18, "type": 8, "unit": 0}]},  # not weight
        grp(5, TS + 4, 70, category=2),  # a goal, not a measurement
        {"grpid": 6, "date": TS + 5, "modified": TS + 5, "measures": "garbage"},
    ]
    got = wclient.weights(0)
    assert [(g.grpid, g.weight_kg) for g in got] == [(1, 72.5), (2, 72.51), (3, 72.5)]
    assert got[0].measured_at == datetime.fromtimestamp(TS, UTC)


def test_the_secret_and_tokens_are_redacted_in_logs(wclient, connected, caplog):
    import logging

    caplog.set_level(logging.INFO)
    logging.getLogger("apps.fitness.withings").warning(
        "debug %s and %s", SECRET, connected.refresh_token
    )
    assert SECRET not in caplog.text and connected.refresh_token not in caplog.text
    assert SECRET not in repr(wclient)
    assert "refresh-0" not in repr(connected)
