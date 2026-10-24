from fastapi.testclient import TestClient

from app.api.routes import get_db
from app.importers.strong.importer import run_import
from app.main import app


def test_health_and_sessions(db, options):
    run_import(db, options)
    db.commit()
    app.dependency_overrides[get_db] = lambda: db
    try:
        client = TestClient(app)
        assert client.get("/health").json() == {"status": "ok"}
        sessions = client.get("/api/v1/sessions").json()
        assert len(sessions) == 4
        variants = client.get("/api/v1/variants").json()
        squat = next(v for v in variants if v["name"] == "Squat (Barbell)")
        stats = client.get(f"/api/v1/variants/{squat['id']}/stats").json()
        assert len(stats) == 2
        assert client.get("/api/v1/variants/9999/stats").status_code == 404
    finally:
        app.dependency_overrides.clear()
