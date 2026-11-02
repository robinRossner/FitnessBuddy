"""The fitness API is served under `/api/v1/...` (installed iOS app) and `/api/v1/fitness/...`.

Both prefixes mount the same routers: same handlers, same bearer auth. `/health` stays unprefixed.
"""

from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient

from apps.fitness.api.deps import get_db
from apps.fitness.importers.strong.importer import run_import
from core.auth import get_api_token
from core.settings import get_core_settings
from server import app

LEGACY = "/api/v1"
FITNESS = "/api/v1/fitness"
PUBLIC = {"/health", f"{LEGACY}/health", f"{FITNESS}/health"}
TOKEN = "prefix-test-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


def _operations() -> list[tuple[str, str]]:
    paths = app.openapi()["paths"]
    return sorted((m.upper(), p) for p, item in paths.items() for m in item)


def _fill(path: str) -> str:
    return re.sub(r"\{[^}]+\}", "1", path)


@pytest.fixture
def client(db, monkeypatch):
    monkeypatch.setenv("API_TOKEN", TOKEN)
    get_core_settings.cache_clear()  # auth reads the real settings here, no dependency override
    app.dependency_overrides[get_db] = lambda: db
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()
        get_core_settings.cache_clear()


def test_every_legacy_route_has_a_fitness_twin():
    ops = set(_operations())
    assert not any(p.startswith(LEGACY + "/receipts") for _, p in ops)  # receipts moved out
    legacy = {(m, p) for m, p in ops if p.startswith(LEGACY + "/") and not p.startswith(FITNESS)}
    assert len(legacy) > 40
    for method, path in legacy:
        assert (method, FITNESS + path.removeprefix(LEGACY)) in ops, (method, path)
    # nothing else got a prefix: the only other route is the unprefixed /health
    assert {p for _, p in ops if not p.startswith(LEGACY)} == {"/health"}


def test_health_is_public_on_every_prefix(client):
    assert client.get("/health").json() == {"status": "ok"}
    for prefix in (LEGACY, FITNESS):
        body = client.get(f"{prefix}/health").json()
        assert body["status"] == "ok" and "version" in body


@pytest.mark.parametrize(("method", "path"), [op for op in _operations() if op[1] not in PUBLIC])
def test_every_route_requires_the_token(client, method, path):
    url = _fill(path)
    assert client.request(method, url).status_code == 401
    assert client.request(method, url, headers={"Authorization": "Bearer nope"}).status_code == 401


def test_both_prefixes_serve_the_same_data(client, db, options):
    run_import(db, options)
    db.commit()
    for suffix in ("/sessions", "/variants", "/variants/1/stats"):
        legacy = client.get(LEGACY + suffix, headers=AUTH)
        fitness = client.get(FITNESS + suffix, headers=AUTH)
        assert legacy.status_code == fitness.status_code == 200, suffix
        assert legacy.json() == fitness.json(), suffix
    assert len(client.get(f"{FITNESS}/sessions", headers=AUTH).json()) == 4
    assert client.get(f"{FITNESS}/variants/9999/stats", headers=AUTH).status_code == 404


def test_dependency_override_applies_to_both_prefixes(db):
    """The auth dependency is the one object, so overriding it covers both mounts."""
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_api_token] = lambda: "t"
    try:
        c = TestClient(app)
        for prefix in (LEGACY, FITNESS):
            assert c.get(f"{prefix}/sessions").status_code == 401
            assert (
                c.get(f"{prefix}/sessions", headers={"Authorization": "Bearer t"}).status_code
                == 200
            )
    finally:
        app.dependency_overrides.clear()
