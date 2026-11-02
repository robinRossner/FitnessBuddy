"""HTTP API tests: auth and its throttle, route prefixes, exercises endpoints."""

import pytest


@pytest.fixture(autouse=True)
def _isolation(auth_throttle_off, clean_settings):
    """See tests/conftest.py: bad bearer tokens; fresh settings caches and app overrides."""
