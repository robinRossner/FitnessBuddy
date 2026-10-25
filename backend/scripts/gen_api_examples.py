"""Regenerate docs/api/examples/*.json by calling the real endpoints (FastAPI TestClient) against
a COPY of the real database, so the shapes, ids and numbers are exactly what the server emits.

    cd backend && uv run python -m scripts.gen_api_examples [--db var/fitness.db]

The live database is never written; the engine re-run hook is stubbed. Lists in the pull example
are trimmed (newest 3 sessions + their sets) to keep the fixture small.
"""

from __future__ import annotations

import argparse
import json
import shutil
import tempfile
import uuid
from pathlib import Path
from unittest.mock import patch

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient

from app.api.auth import get_api_token
from app.api.routes import get_db
from app.db.session import make_engine
from app.main import app
from app.settings import BACKEND_DIR, REPO_ROOT

OUT = REPO_ROOT / "docs" / "api" / "examples"
TOKEN = "example-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
MONDAY = "2026-10-26"  # the upper day of the example week
NS = uuid.UUID("c3f0b2a4-6d1e-4c57-9a3b-0e4f5d6a7b81")


def fid(name: str) -> str:
    return str(uuid.uuid5(NS, name))


def dump(name: str, data) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / name).write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print("wrote", OUT / name)


def trim(page: dict, keep_sessions: int = 3) -> dict:
    page = dict(page)
    page["sessions"] = sorted(page["sessions"], key=lambda s: s["started_at"])[-keep_sessions:]
    ids = {s["id"] for s in page["sessions"]}
    page["sets"] = [s for s in page["sets"] if s["session_id"] in ids]
    page["_note"] = (
        f"example only: history trimmed to the newest {keep_sessions} sessions; a real snapshot "
        "holds the last 120 days"
    )
    return page


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(BACKEND_DIR / "var" / "fitness.db"))
    args = ap.parse_args()

    tmp = Path(tempfile.mkdtemp()) / "copy.db"
    shutil.copy(args.db, tmp)
    url = f"sqlite:///{tmp}"
    alembic_cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    alembic_cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    alembic_cfg.attributes["url"] = url
    command.upgrade(alembic_cfg, "head")  # the copy gets the newest schema
    engine = make_engine(url)
    from sqlalchemy.orm import sessionmaker

    factory = sessionmaker(bind=engine, expire_on_commit=False)

    def _db():
        db = factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_api_token] = lambda: TOKEN
    client = TestClient(app)

    from datetime import date

    with (
        patch("app.api.engine._today", lambda: date.fromisoformat(MONDAY)),
        patch("app.engine.service.user_today", lambda s=None: date.fromisoformat(MONDAY)),
        patch("app.api.sync.rerun_engine", lambda *a, **k: None),
    ):
        from app.engine import service as engine_service

        plan_db = factory()  # the stored plans of the copy come from the CURRENT engine
        engine_service.run_engine(plan_db, today=date.fromisoformat(MONDAY), weeks=2)
        plan_db.commit()
        plan_db.close()
        today = client.get("/api/v1/plan/today", headers=AUTH).json()
        week = client.get(f"/api/v1/plan/week?start={MONDAY}", headers=AUTH).json()
        dump("plan_today.json", today)
        dump("plan_week.json", week)

        snapshot = client.get("/api/v1/sync/pull", headers=AUTH, params={"limit": 5000}).json()
        assert not snapshot["has_more"]
        dump("sync_pull.json", trim(snapshot))

        # ---- push: the Monday upper session as the app would log it, linked to its plan
        plan = next(s for s in today["sessions"] if s["kind"] == "upper")
        lifts = plan["session"]["exercises"]
        sid = fid("example-session")
        sets = []
        for ex_i, ex in enumerate([e for e in lifts if e.get("variant_id")][:3]):
            for s in ex["sets"]:
                if s["target_weight"] is None:
                    continue
                n = len(sets)
                sets.append(
                    {
                        "id": fid(f"example-set-{n}"),
                        "session_id": sid,
                        "variant_id": ex["variant_id"],
                        "exercise_order": ex_i,
                        "set_order": s["index"],
                        "set_type": s["set_type"],
                        "weight_kg": s["target_weight"],
                        "reps": s["target_reps"],
                        "duration_s": None,
                        "completed_at": f"2026-10-26T16:{35 + n:02d}:30.000Z",
                        "notes": None,
                        "updated_at": f"2026-10-26T16:{35 + n:02d}:31.000Z",
                        "deleted_at": None,
                    }
                )
        request = {
            "device_id": "iphone-demo",
            "sessions": [
                {
                    "id": sid,
                    "planned_session_id": plan["id"],
                    "gym_id": plan.get("gym_id") or 4,
                    "kind": "upper",
                    "name": plan["title"] or "Upper",
                    "started_at": "2026-10-26T16:30:12.000Z",
                    "ended_at": "2026-10-26T17:24:40.000Z",
                    "notes": None,
                    "updated_at": "2026-10-26T17:24:41.000Z",
                    "deleted_at": None,
                }
            ],
            "sets": sets,
            "body_measurements": [
                {
                    "id": fid("example-weight"),
                    "measured_at": "2026-10-26T06:45:00.000Z",
                    "weight_kg": 82.4,
                    "note": None,
                    "updated_at": "2026-10-26T06:45:02.000Z",
                    "deleted_at": None,
                }
            ],
        }
        dump("sync_push_request.json", request)
        response = client.post("/api/v1/sync/push", headers=AUTH, json=request)
        assert response.status_code == 200, response.text
        dump("sync_push_response.json", response.json())

        delta = client.get(
            "/api/v1/sync/pull", headers=AUTH, params={"since": snapshot["cursor"]}
        ).json()
        dump("sync_pull_incremental.json", delta)

        vid = request["sets"][0]["variant_id"]
        hist = client.get(f"/api/v1/variants/{vid}/history?limit=5", headers=AUTH).json()
        dump("variant_history.json", hist)
    app.dependency_overrides.clear()
    shutil.rmtree(tmp.parent, ignore_errors=True)


if __name__ == "__main__":
    main()
