"""Gym tests: gyms.yaml import, machines and variant endpoints."""

import pytest


@pytest.fixture(autouse=True)
def _isolation(auth_throttle_off):
    """See tests/conftest.py: tests here send bad bearer tokens through the app."""
