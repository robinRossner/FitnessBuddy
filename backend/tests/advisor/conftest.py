"""Advisor tests: Claude coach (fake client), summary, validator, consultations, jobs."""

import pytest


@pytest.fixture(autouse=True)
def _isolation(auth_throttle_off, clean_settings):
    """See tests/conftest.py: bad bearer tokens; fresh settings caches and app overrides."""


@pytest.fixture
def logs(db, caplog):
    """caplog that works after alembic's fileConfig disabled the app loggers."""
    import logging

    for name, lg in logging.root.manager.loggerDict.items():
        if name.startswith("apps.") and isinstance(lg, logging.Logger):
            lg.disabled = False
    caplog.set_level(logging.INFO, logger="apps")
    return caplog
