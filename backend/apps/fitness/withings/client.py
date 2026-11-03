"""Client for the Withings Public API (OAuth 2.0 + `measure` / getmeas), weight only.

- Authorize: `https://account.withings.com/oauth2_user/authorize2` (scope `user.metrics`).
- Token: `POST https://wbsapi.withings.net/v2/oauth2` (`action=requesttoken`), form-encoded.
- Measures: `POST https://wbsapi.withings.net/measure` (`action=getmeas`, `meastype=1`) with a
  bearer access token; paged by `more` / `offset`.
- Withings answers HTTP 200 with a non-zero `status` for most errors. Status 0 = ok, 601 = too
  many requests, the 100-102 / 200-201 / 214-221 / 401 family = the token is not accepted (best
  effort list: anything else non-zero counts as a plain failure and keeps the stored token).
- The access token (3 h) is refreshed when under 5 minutes are left, or once after a rejection.
  Refresh tokens rotate, so the new pair is saved before the access token is used.
- Errors never contain tokens, codes or the client secret.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlencode

import httpx

from apps.fitness.withings.errors import (
    NotConnectedError,
    RateLimitedError,
    TokenRevokedError,
    WithingsError,
)
from apps.fitness.withings.tokens import Tokens, TokenStore
from core.redact import install_log_redaction, redact, register_secret

log = logging.getLogger(__name__)

AUTHORIZE_URL = "https://account.withings.com/oauth2_user/authorize2"
TOKEN_URL = "https://wbsapi.withings.net/v2/oauth2"
MEASURE_URL = "https://wbsapi.withings.net/measure"
SCOPE = "user.metrics"
REFRESH_WINDOW_S = 300
RATE_LIMITED = 601
AUTH_STATUSES = frozenset({100, 101, 102, 200, 201, 214, 215, 216, 217, 218, 219, 220, 221, 401})
INVALID_GRANT_STATUSES = AUTH_STATUSES | {293, 503}  # a refresh / code Withings refuses
WEIGHT_TYPE = 1
MAX_PAGES = 50

# Refresh tokens rotate: two threads refreshing at once would invalidate each other.
_refresh_lock = threading.Lock()


@dataclass(frozen=True)
class WeightGroup:
    """One weigh-in: Withings measure group `grpid`, taken at `measured_at` (UTC)."""

    grpid: int
    measured_at: datetime
    weight_kg: float


class WithingsClient:
    def __init__(
        self,
        client_id: str,
        client_secret: str,
        redirect_uri: str,
        store: TokenStore,
        *,
        timeout_s: float = 30.0,
        transport: httpx.BaseTransport | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        install_log_redaction()
        self.client_id = client_id
        self._secret = client_secret
        register_secret(client_secret)
        self.redirect_uri = redirect_uri
        self.store = store
        self._clock = clock
        self._http = httpx.Client(timeout=timeout_s, transport=transport)

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> WithingsClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def __repr__(self) -> str:  # never include the secret
        return f"WithingsClient(client_id={self.client_id!r})"

    # ------------------------------------------------------------------ authorize + tokens

    def authorize_url(self, state: str) -> str:
        query = urlencode(
            {
                "response_type": "code",
                "client_id": self.client_id,
                "scope": SCOPE,
                "redirect_uri": self.redirect_uri,
                "state": state,
            }
        )
        return f"{AUTHORIZE_URL}?{query}"

    def exchange_code(self, code: str) -> Tokens:
        """Authorization code -> tokens, saved. Raises WithingsError when Withings refuses."""
        register_secret(code)
        body = self._token_request(
            {"grant_type": "authorization_code", "code": code, "redirect_uri": self.redirect_uri},
            refusal_is_revocation=False,
        )
        tokens = self._tokens_from(body)
        self.store.save(tokens)
        log.info("Withings connected (userid %s)", tokens.userid)
        return tokens

    def _tokens_from(self, body: dict[str, Any]) -> Tokens:
        try:
            return Tokens(
                userid=str(body["userid"]),
                access_token=str(body["access_token"]),
                refresh_token=str(body["refresh_token"]),
                expires_at=self._clock() + float(body["expires_in"]),
                scope=str(body.get("scope", "")),
            )
        except (KeyError, TypeError, ValueError):
            raise WithingsError("Withings token response is incomplete") from None

    def _token_request(self, params: dict[str, str], *, refusal_is_revocation: bool) -> Any:
        data = {
            "action": "requesttoken",
            "client_id": self.client_id,
            "client_secret": self._secret,
            **params,
        }
        resp = self._post(TOKEN_URL, data=data, headers={})
        return self._unwrap(
            resp, revoked_statuses=INVALID_GRANT_STATUSES if refusal_is_revocation else frozenset()
        )

    def access_token(self, *, force_refresh: bool = False) -> str:
        """A usable access token, refreshed first when about to expire (or `force_refresh`)."""
        tokens = self.store.load()
        if tokens is None:
            raise NotConnectedError("Withings is not connected")
        if not force_refresh and tokens.expires_at - self._clock() > REFRESH_WINDOW_S:
            return tokens.access_token
        with _refresh_lock:
            # another thread may have refreshed (and rotated) while we waited
            current = self.store.load()
            if current is None:
                raise NotConnectedError("Withings is not connected")
            if (
                current.refresh_token == tokens.refresh_token
                or current.expires_at - self._clock() <= REFRESH_WINDOW_S
            ):
                current = self._refresh(current)
            return current.access_token

    def _refresh(self, tokens: Tokens) -> Tokens:
        body = self._token_request(
            {"grant_type": "refresh_token", "refresh_token": tokens.refresh_token},
            refusal_is_revocation=True,
        )
        fresh = self._tokens_from(body)
        self.store.save(fresh)  # persist the rotated refresh token before anything uses it
        log.info("refreshed the Withings access token")
        return fresh

    # ------------------------------------------------------------------ requests

    def _post(self, url: str, *, data: dict[str, Any], headers: dict[str, str]) -> httpx.Response:
        try:
            resp = self._http.post(url, data=data, headers=headers)
        except httpx.TransportError as exc:
            raise WithingsError(f"Withings unreachable ({type(exc).__name__})") from None
        if resp.status_code == 429:
            raise RateLimitedError("Withings rate limit (HTTP 429)", _retry_after(resp))
        if resp.status_code >= 400:
            raise WithingsError(f"Withings HTTP {resp.status_code}")
        return resp

    @staticmethod
    def _unwrap(resp: httpx.Response, *, revoked_statuses: frozenset[int]) -> Any:
        """The `body` of a status-0 reply; non-zero statuses become typed errors."""
        try:
            payload = resp.json()
            status = int(payload["status"])
        except (ValueError, KeyError, TypeError):
            raise WithingsError("Withings sent an unreadable response") from None
        if status == 0:
            body = payload.get("body")
            return body if body is not None else {}
        detail = redact(str(payload.get("error", "")))[:100]
        message = f"Withings status {status}" + (f" ({detail})" if detail else "")
        if status == RATE_LIMITED:
            raise RateLimitedError(message, _retry_after(resp))
        if status in revoked_statuses:
            raise TokenRevokedError(message)
        raise _Rejected(message, status)

    # ------------------------------------------------------------------ measures

    def weights(self, lastupdate: int) -> list[WeightGroup]:
        """Weigh-ins (meastype 1, real measurements) modified since the unix time `lastupdate`."""
        refreshed = False
        while True:
            try:
                return self._weights(self.access_token(force_refresh=False), lastupdate)
            except _Rejected as exc:
                if exc.status not in AUTH_STATUSES:
                    raise WithingsError(str(exc)) from None
                if refreshed:
                    raise WithingsError(f"{exc} after a token refresh") from None
                refreshed = True
                self.access_token(force_refresh=True)  # TokenRevokedError when it is gone

    def _weights(self, access: str, lastupdate: int) -> list[WeightGroup]:
        out: dict[int, WeightGroup] = {}
        offset: int | None = None
        for _ in range(MAX_PAGES):
            data: dict[str, Any] = {
                "action": "getmeas",
                "meastype": WEIGHT_TYPE,
                "category": 1,
                "lastupdate": lastupdate,
            }
            if offset is not None:
                data["offset"] = offset
            resp = self._post(MEASURE_URL, data=data, headers={"Authorization": f"Bearer {access}"})
            body = self._unwrap(resp, revoked_statuses=frozenset())
            for group in body.get("measuregrps") or []:
                parsed = _weight_group(group)
                if parsed is not None:
                    out[parsed.grpid] = parsed
            if not body.get("more"):
                break
            offset = body.get("offset")
            if offset is None:
                break
        return sorted(out.values(), key=lambda g: g.measured_at)


class _Rejected(WithingsError):
    """Internal: a non-zero Withings status on a data call (the caller decides what it means)."""

    def __init__(self, message: str, status: int):
        super().__init__(message)
        self.status = status


def _weight_group(group: dict[str, Any]) -> WeightGroup | None:
    """kg = value * 10**unit of the type-1 measure; None if the group has no usable weight."""
    try:
        if int(group.get("category", 1)) != 1:
            return None
        for m in group["measures"]:
            if int(m["type"]) == WEIGHT_TYPE:
                return WeightGroup(
                    grpid=int(group["grpid"]),
                    measured_at=datetime.fromtimestamp(int(group["date"]), UTC),
                    weight_kg=round(float(m["value"]) * 10 ** int(m["unit"]), 3),
                )
    except (KeyError, TypeError, ValueError, OverflowError, OSError):
        return None
    return None


def _retry_after(resp: httpx.Response) -> float | None:
    try:
        return max(0.0, float(resp.headers["Retry-After"]))
    except (KeyError, ValueError):
        return None
