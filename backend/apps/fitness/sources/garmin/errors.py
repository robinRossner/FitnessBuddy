"""Garmin source errors. Messages never contain keys, tokens or request headers."""

from __future__ import annotations


class GarminError(Exception):
    """Base class."""


class SourceUnavailable(GarminError):
    """The source cannot serve requests at all (auth / key / blocked): fallback-eligible."""


class KeyExpiredError(SourceUnavailable):
    """The Personal API key was rejected: expired, deleted or invalid. Create a new one."""


class RateLimitedError(GarminError):
    """429 persisted after all retries. Wait (see `retry_after`) and resume."""

    def __init__(self, message: str, retry_after: float | None = None):
        super().__init__(message)
        self.retry_after = retry_after


class NotFoundError(GarminError):
    """404: the resource does not exist (e.g. no FIT file for a manual activity)."""


class BadRequestError(GarminError):
    """400: usually a date window above the endpoint's maximum, or a malformed parameter."""


class ServerError(GarminError):
    """5xx / network errors persisted after all retries."""
