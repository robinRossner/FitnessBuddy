"""Stateless OAuth state: sign / verify / expiry / tampering."""

from __future__ import annotations

import pytest

from apps.fitness.withings.errors import InvalidStateError
from apps.fitness.withings.state import MAX_AGE_S, sign_state, verify_state

KEY = "client-secret-key"
NOW = 1_800_000_000


@pytest.mark.parametrize("client", ["app", "web"])
def test_roundtrip(client):
    state = sign_state(KEY, client, now=NOW)
    assert verify_state(KEY, state, now=NOW + 5) == client


def test_states_are_unique_and_url_safe():
    a, b = sign_state(KEY, "app", now=NOW), sign_state(KEY, "app", now=NOW)
    assert a != b
    assert all(c.isalnum() or c in ".-_" for c in a)


def test_expires_after_ten_minutes():
    state = sign_state(KEY, "app", now=NOW)
    assert verify_state(KEY, state, now=NOW + MAX_AGE_S) == "app"
    with pytest.raises(InvalidStateError, match="expired"):
        verify_state(KEY, state, now=NOW + MAX_AGE_S + 1)


def test_state_from_the_future_is_rejected():
    state = sign_state(KEY, "app", now=NOW + 3600)
    with pytest.raises(InvalidStateError):
        verify_state(KEY, state, now=NOW)


def test_tampered_client_or_time_fails_the_mac():
    issued, client, nonce, mac = sign_state(KEY, "app", now=NOW).split(".")
    for forged in (
        f"{issued}.web.{nonce}.{mac}",
        f"{int(issued) + 1}.{client}.{nonce}.{mac}",
        f"{issued}.{client}.other.{mac}",
        f"{issued}.{client}.{nonce}.{mac[:-1]}{'B' if mac[-1] == 'A' else 'A'}",
    ):
        with pytest.raises(InvalidStateError):
            verify_state(KEY, forged, now=NOW)


def test_wrong_key_and_garbage():
    state = sign_state(KEY, "web", now=NOW)
    with pytest.raises(InvalidStateError):
        verify_state("another-key", state, now=NOW)
    for junk in ("", "a.b.c", "x" * 40, "1.2.3.4.5", "abc.app.n.m"):
        with pytest.raises(InvalidStateError):
            verify_state(KEY, junk, now=NOW)


def test_unknown_client_cannot_be_signed():
    with pytest.raises(ValueError):
        sign_state(KEY, "other")
