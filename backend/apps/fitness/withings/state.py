"""Stateless OAuth `state`: `{issued_at}.{client}.{nonce}.{mac}`.

The mac is HMAC-SHA256 (key = the Withings client secret) over `{issued_at}.{client}.{nonce}`, so
the callback needs no server-side storage. `client` says who started the flow: the iOS app
(`app`: the callback redirects back into it) or a browser (`web`: it shows a small page).
A state is valid for `MAX_AGE_S` seconds.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time

from apps.fitness.withings.errors import InvalidStateError

CLIENTS = ("app", "web")
MAX_AGE_S = 600
_FUTURE_SKEW_S = 60


def _mac(key: str, payload: str) -> str:
    digest = hmac.new(key.encode(), payload.encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")


def sign_state(key: str, client: str, *, now: float | None = None, nonce: str | None = None) -> str:
    if client not in CLIENTS:
        raise ValueError(f"client must be one of {CLIENTS}")
    issued = int(time.time() if now is None else now)
    payload = f"{issued}.{client}.{nonce or secrets.token_urlsafe(12)}"
    return f"{payload}.{_mac(key, payload)}"


def verify_state(key: str, state: str, *, now: float | None = None) -> str:
    """The client (`app` | `web`) a valid state was issued for; InvalidStateError otherwise."""
    parts = state.split(".")
    if len(parts) != 4:
        raise InvalidStateError("malformed state")
    issued_s, client, nonce, mac = parts
    expected = _mac(key, f"{issued_s}.{client}.{nonce}")
    if not hmac.compare_digest(mac.encode(), expected.encode()):
        raise InvalidStateError("bad state signature")
    if client not in CLIENTS:
        raise InvalidStateError("unknown client")
    try:
        age = (time.time() if now is None else now) - int(issued_s)
    except ValueError:
        raise InvalidStateError("malformed state") from None
    if age > MAX_AGE_S or age < -_FUTURE_SKEW_S:
        raise InvalidStateError("state expired")
    return client
