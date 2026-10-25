"""Regenerate docs/api/examples/gyms_list.json and gym_machines.json by calling the real gym
endpoints (FastAPI TestClient) against a COPY of the real database (read-only calls only).

    cd backend && uv run python -m scripts.gen_gym_examples [--db var/fitness.db]

The live database is never written. gym_machines.json is the machine list of the current gym.
"""

from __future__ import annotations

import argparse
import json
import shutil
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app.api.auth import get_api_token
from app.api.routes import get_db
from app.db.session import make_engine
from app.main import app
from app.settings import BACKEND_DIR, REPO_ROOT

OUT = REPO_ROOT / "docs" / "api" / "examples"
TOKEN = "example-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


def dump(name: str, data) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / name).write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {name} ({len(json.dumps(data)) / 1000:.1f} KB)")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(BACKEND_DIR / "var" / "fitness.db"))
    args = ap.parse_args()

    tmp = Path(tempfile.mkdtemp()) / "copy.db"
    shutil.copy(args.db, tmp)
    factory = sessionmaker(bind=make_engine(f"sqlite:///{tmp}"), expire_on_commit=False)

    def _db():
        db = factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_api_token] = lambda: TOKEN
    client = TestClient(app)
    listing = client.get("/api/v1/gyms", headers=AUTH)
    assert listing.status_code == 200, listing.text
    gyms = listing.json()
    dump("gyms_list.json", gyms)
    gym_id = gyms["current_gym_id"] or gyms["gyms"][-1]["id"]
    machines = client.get(f"/api/v1/gyms/{gym_id}/machines", headers=AUTH)
    assert machines.status_code == 200, machines.text
    dump("gym_machines.json", machines.json())
    app.dependency_overrides.clear()
    shutil.rmtree(tmp.parent, ignore_errors=True)


if __name__ == "__main__":
    main()
