"""A scripted Withings server for `httpx.MockTransport`, and a settable clock."""

from __future__ import annotations

from typing import Any
from urllib.parse import parse_qs

import httpx

from apps.fitness.withings.client import MEASURE_URL, TOKEN_URL
from apps.fitness.withings.tokens import TokenStore

CLIENT_ID = "test-client-id"
SECRET = "test-client-secret-0123456789"
REDIRECT = "https://example.test/api/v1/fitness/withings/callback"


class Clock:
    def __init__(self, now: float = 1_800_000_000.0) -> None:
        self.now = now

    def time(self) -> float:
        return self.now


def grp(grpid: int, ts: int, kg: float, *, category: int = 1, mtype: int = 1) -> dict[str, Any]:
    """A Withings measure group with one measure (value * 10**unit = kg)."""
    return {
        "grpid": grpid,
        "date": ts,
        "created": ts,
        "modified": ts,
        "category": category,
        "deviceid": "dev",
        "measures": [{"value": round(kg * 1000), "type": mtype, "unit": -3}],
    }


def ok(body: dict[str, Any]) -> httpx.Response:
    return httpx.Response(200, json={"status": 0, "body": body})


def fail(status: int, error: str = "") -> httpx.Response:
    return httpx.Response(200, json={"status": status, "error": error})


class FakeWithings:
    """Token endpoint (rotating refresh tokens) + `getmeas` over `groups`."""

    def __init__(self, clock: Clock) -> None:
        self.clock = clock
        self.groups: list[dict[str, Any]] = []
        self.page_size: int | None = None
        self.token_requests: list[dict[str, str]] = []
        self.measure_requests: list[dict[str, str]] = []
        self.auth_headers: list[str] = []
        self.token_replies: list[httpx.Response] = []  # consumed first, then the default
        self.measure_replies: list[httpx.Response] = []
        self.refreshes = 0
        self.store: TokenStore | None = None  # when set: checked on every measure call
        self.expect_disk_refresh: str | None = None

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
        url = str(request.url)
        if url == TOKEN_URL:
            self.token_requests.append(form)
            if self.token_replies:
                return self.token_replies.pop(0)
            if form["grant_type"] == "refresh_token":
                self.refreshes += 1
            n = self.refreshes
            return ok(
                {
                    "userid": "4711",
                    "access_token": f"access-{n}-abcdefgh",
                    "refresh_token": f"refresh-{n}-abcdefgh",
                    "expires_in": 10800,
                    "scope": "user.metrics",
                    "token_type": "Bearer",
                }
            )
        assert url == MEASURE_URL, url
        self.measure_requests.append(form)
        self.auth_headers.append(request.headers["authorization"])
        if self.store is not None and self.expect_disk_refresh:
            on_disk = self.store.load()
            assert on_disk is not None and on_disk.refresh_token == self.expect_disk_refresh
        if self.measure_replies:
            return self.measure_replies.pop(0)
        since = int(form["lastupdate"])
        rows = [g for g in self.groups if g["modified"] >= since]
        if self.page_size is None:
            return ok({"measuregrps": rows, "more": 0, "offset": 0})
        start = int(form.get("offset", 0))
        page = rows[start : start + self.page_size]
        more = start + self.page_size < len(rows)
        return ok({"measuregrps": page, "more": 1 if more else 0, "offset": start + self.page_size})
