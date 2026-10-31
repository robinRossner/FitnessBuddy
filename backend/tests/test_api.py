from fastapi.testclient import TestClient

from apps.fitness.api.routes import get_db
from apps.fitness.importers.strong.importer import run_import
from core.auth import get_api_token
from server import app


def test_health_and_sessions(db, options):
    run_import(db, options)
    db.commit()
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_api_token] = lambda: "t"
    auth = {"Authorization": "Bearer t"}
    try:
        client = TestClient(app)
        assert client.get("/health").json() == {"status": "ok"}  # public
        for path in ("/api/v1/sessions", "/api/v1/variants", "/api/v1/variants/1/stats"):
            assert client.get(path).status_code == 401
            assert client.get(path, headers={"Authorization": "Bearer x"}).status_code == 401
        sessions = client.get("/api/v1/sessions", headers=auth).json()
        assert len(sessions) == 4
        variants = client.get("/api/v1/variants", headers=auth).json()
        squat = next(v for v in variants if v["name"] == "Squat (Barbell)")
        stats = client.get(f"/api/v1/variants/{squat['id']}/stats", headers=auth).json()
        assert len(stats) == 2
        assert client.get("/api/v1/variants/9999/stats", headers=auth).status_code == 404
    finally:
        app.dependency_overrides.clear()
