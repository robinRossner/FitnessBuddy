"""Low-level client for the official Garmin Connect Personal API (pull-only).

Verified against Garmin's published OpenAPI spec and the official python client
(github.com/garmin/connect-personal-api-python); see docs/garmin-personal-api.md.

- Token exchange: `POST {base}/personalapitoken-service/personal-api/personal-api-key/token`
  with `Authorization: ApiKey <key>`; the body is a JWT bearer token valid <= 24 h (or until the
  key expires). No refresh tokens: exchange again.
- The token is cached on disk (mode 0600, fingerprinted to key+base URL) and renewed when less
  than an hour is left.
- Requests are paced (`interval_s`), retried with exponential backoff + jitter on 429 / 5xx /
  network errors (Retry-After respected), and a 401 triggers exactly one forced token refresh.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import random
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any

import httpx

from app.sources.garmin.errors import (
    BadRequestError,
    GarminError,
    KeyExpiredError,
    NotFoundError,
    RateLimitedError,
    ServerError,
    SourceUnavailable,
)
from app.sources.garmin.redact import install_log_redaction, redact, register_secret

log = logging.getLogger(__name__)

TOKEN_PATH = "/personalapitoken-service/personal-api/personal-api-key/token"
REFRESH_WINDOW_S = 3600
KEY_PAGE = "https://connect.garmin.com/app/settings/personalApiToken"

Params = Mapping[str, str | int | float | bool]


@dataclass(frozen=True)
class BearerToken:
    access_token: str = field(repr=False)
    expires_at: float  # epoch seconds (JWT exp)


def jwt_claims(token: str) -> dict[str, Any]:
    """Decode a JWT payload without verifying it (we only need `exp`)."""
    try:
        payload = token.split(".")[1]
        return json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    except (IndexError, ValueError) as exc:
        raise GarminError("token endpoint did not return a JWT") from exc


def _retry_after_s(resp: httpx.Response, now: datetime) -> float | None:
    raw = resp.headers.get("Retry-After")
    if not raw:
        return None
    try:
        return max(0.0, float(raw))
    except ValueError:
        try:
            return max(0.0, (parsedate_to_datetime(raw) - now).total_seconds())
        except (TypeError, ValueError):
            return None


class PersonalApiClient:
    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = "https://personal.connectapi.garmin.com",
        token_cache_path: Path | None = None,
        interval_s: float = 1.0,
        timeout_s: float = 30.0,
        max_retries: int = 5,
        max_retry_wait_s: float = 120.0,
        transport: httpx.BaseTransport | None = None,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
        jitter: Callable[[], float] = random.random,
    ) -> None:
        if not api_key or not api_key.strip():
            raise KeyExpiredError("GARMIN_PERSONAL_API_KEY is empty")
        install_log_redaction()
        self._key = api_key.strip()
        register_secret(self._key)
        self.base_url = base_url.rstrip("/")
        self.token_cache_path = token_cache_path
        self.interval_s = interval_s
        self.max_retries = max_retries
        self.max_retry_wait_s = max_retry_wait_s
        self._clock = clock
        self._sleep = sleep
        self._jitter = jitter
        self._last_request = 0.0
        self._token: BearerToken | None = None
        self._http = httpx.Client(timeout=timeout_s, transport=transport)
        self._fingerprint = hashlib.sha256(f"{self.base_url}\0{self._key}".encode()).hexdigest()
        self.requests_made = 0

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> PersonalApiClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def __repr__(self) -> str:  # never include the key
        return f"PersonalApiClient(base_url={self.base_url!r})"

    # ------------------------------------------------------------------ token

    def _read_cache(self) -> BearerToken | None:
        if self.token_cache_path is None:
            return None
        try:
            data = json.loads(self.token_cache_path.read_text(encoding="utf-8"))
            if data["fingerprint"] != self._fingerprint:
                return None
            register_secret(data["access_token"])
            return BearerToken(data["access_token"], float(data["expires_at"]))
        except (OSError, KeyError, TypeError, ValueError):
            return None

    def _write_cache(self, token: BearerToken) -> None:
        if self.token_cache_path is None:
            return
        path = self.token_cache_path
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        os.chmod(path, 0o600)  # also tightens a pre-existing file
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(
                {
                    "fingerprint": self._fingerprint,
                    "access_token": token.access_token,
                    "expires_at": token.expires_at,
                },
                fh,
            )

    def token(self, *, force: bool = False) -> BearerToken:
        """A valid bearer token: memory, then disk cache, then a fresh exchange."""
        if not force:
            for tok in (self._token, self._read_cache()):
                if tok and tok.expires_at - self._clock() > REFRESH_WINDOW_S:
                    self._token = tok
                    return tok
        self._token = self._exchange()
        return self._token

    def _exchange(self) -> BearerToken:
        url = f"{self.base_url}{TOKEN_PATH}"
        resp = self._send("POST", url, headers={"Authorization": f"ApiKey {self._key}"})
        if resp.status_code in (401, 403):
            raise KeyExpiredError(
                "Garmin rejected the Personal API key (expired, deleted or invalid). "
                f"Create a new key at {KEY_PAGE} and set GARMIN_PERSONAL_API_KEY."
            )
        self._raise_for_status(resp)
        access = resp.text.strip()
        if access.startswith('"') and access.endswith('"'):
            access = access.strip('"')
        register_secret(access)
        tok = BearerToken(access, float(jwt_claims(access)["exp"]))
        self._write_cache(tok)
        log.info("exchanged Personal API key for a bearer token")
        return tok

    def token_expires_at(self) -> datetime | None:
        """When the cached/current bearer token expires (<= key expiry; not the key's own date)."""
        tok = self._token or self._read_cache()
        return datetime.fromtimestamp(tok.expires_at, UTC) if tok else None

    # ------------------------------------------------------------------ requests

    def _pace(self) -> None:
        wait = self._last_request + self.interval_s - self._clock()
        if wait > 0:
            self._sleep(wait)

    def _backoff(self, attempt: int, hint: float | None) -> float:
        base = min(self.max_retry_wait_s, 2.0 * (2**attempt))
        wait = hint if hint is not None else base * (0.5 + self._jitter())
        return min(wait, self.max_retry_wait_s)

    def _send(
        self, method: str, url: str, *, headers: dict[str, str], params: Params | None = None
    ) -> httpx.Response:
        """One logical request with pacing and retries for 429 / 5xx / transport errors."""
        last_exc: Exception | None = None
        resp: httpx.Response | None = None
        for attempt in range(self.max_retries + 1):
            self._pace()
            self._last_request = self._clock()
            self.requests_made += 1
            try:
                resp = self._http.request(method, url, headers=headers, params=params)
            except httpx.TransportError as exc:
                last_exc = exc
                resp = None
                if attempt == self.max_retries:
                    break
                wait = self._backoff(attempt, None)
                log.warning("network error (%s); retry in %.0fs", type(exc).__name__, wait)
                self._sleep(wait)
                continue
            if resp.status_code == 429 or resp.status_code >= 500:
                hint = _retry_after_s(resp, datetime.now(UTC))
                if resp.status_code == 429 and hint is not None and hint > self.max_retry_wait_s:
                    raise RateLimitedError(
                        f"rate limited; Garmin asks to wait {hint:.0f}s", retry_after=hint
                    )
                if attempt == self.max_retries:
                    break
                wait = self._backoff(attempt, hint)
                log.warning("HTTP %s from Garmin; retry in %.0fs", resp.status_code, wait)
                self._sleep(wait)
                continue
            return resp
        if resp is not None and resp.status_code == 429:
            raise RateLimitedError(
                f"rate limited (429) after {self.max_retries} retries",
                retry_after=_retry_after_s(resp, datetime.now(UTC)),
            )
        detail = f"HTTP {resp.status_code}" if resp is not None else type(last_exc).__name__
        raise ServerError(f"Garmin unavailable after {self.max_retries} retries ({detail})")

    @staticmethod
    def _raise_for_status(resp: httpx.Response) -> None:
        code = resp.status_code
        if code < 400:
            return
        body = redact(resp.text.strip().replace("\n", " "))[:200]
        if code == 400:
            raise BadRequestError(f"HTTP 400 {body}")
        if code == 404:
            raise NotFoundError(f"HTTP 404 {body}")
        if code in (401, 403):
            raise SourceUnavailable(f"HTTP {code}: not authorised ({body})")
        raise GarminError(f"HTTP {code} {body}")

    def _get_response(self, path: str, params: Params | None, accept: str) -> httpx.Response:
        url = f"{self.base_url}{path}"
        refreshed = False
        while True:
            headers = {"Authorization": f"Bearer {self.token().access_token}", "Accept": accept}
            resp = self._send("GET", url, headers=headers, params=params)
            if resp.status_code == 401 and not refreshed:
                refreshed = True
                self.token(force=True)  # raises KeyExpiredError if the key itself is dead
                continue
            self._raise_for_status(resp)
            return resp

    def get(self, path: str, params: Params | None = None) -> Any:
        """GET JSON. Returns None for 204 / empty bodies (no data)."""
        resp = self._get_response(path, params, "application/json")
        if resp.status_code == 204 or not resp.content:
            return None
        return resp.json()

    def get_bytes(self, path: str) -> bytes:
        resp = self._get_response(
            path, None, "application/zip, application/x-zip-compressed, application/octet-stream"
        )
        if not resp.content:
            raise NotFoundError("empty file response")
        return resp.content
