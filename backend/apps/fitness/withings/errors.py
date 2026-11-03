"""Withings errors. Messages never contain tokens, codes or the client secret."""

from __future__ import annotations


class WithingsError(Exception):
    """Base class: Withings (or the network) failed; the stored connection is still fine."""


class NotConfiguredError(WithingsError):
    """No client id / secret / redirect URI configured."""


class NotConnectedError(WithingsError):
    """No stored token: the user never connected, or disconnected."""


class TokenRevokedError(NotConnectedError):
    """Withings no longer accepts the refresh token (revoked in the Withings app, or expired
    unused): the user has to connect again."""


class RateLimitedError(WithingsError):
    """Withings status 601 / HTTP 429: wait (see `retry_after`, seconds) and try again."""

    def __init__(self, message: str, retry_after: float | None = None):
        super().__init__(message)
        self.retry_after = retry_after


class InvalidStateError(WithingsError):
    """The OAuth `state` is malformed, forged or too old."""
