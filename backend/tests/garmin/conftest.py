"""Garmin tests: client, sync, mapping, workouts push, zones (fakes in tests/support)."""

import pytest


@pytest.fixture(autouse=True)
def _isolation(auth_throttle_off):
    """See tests/conftest.py: tests here send bad bearer tokens through the app."""
