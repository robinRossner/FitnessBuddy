"""Regenerate docs/api/examples/stats_*.json by calling the real stats endpoints (FastAPI
TestClient) against a COPY of the real database, with "today" pinned to 2026-10-25.

    cd backend && uv run python -m apps.fitness.scripts.gen_stats_examples [--db var/fitness.db]
    [--time]

The live database is never written. Long arrays are trimmed (newest kept) and say so in `_note`,
so every file stays small. `--time` also prints cold / warm timings of each endpoint.
"""

from __future__ import annotations

import argparse
import json
import shutil
import tempfile
import time
from datetime import date
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from apps.fitness.analytics import cache
from apps.fitness.api.deps import get_db
from apps.fitness.db.session import make_engine
from apps.fitness.engine.store import config_service as cs
from apps.fitness.settings import BACKEND_DIR, REPO_ROOT, get_settings
from core.auth import get_api_token
from server import app

OUT = REPO_ROOT / "docs" / "api" / "examples"
TODAY = date(2026, 10, 25)
TOKEN = "example-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
MAX_BYTES = 60_000
PACE_KEYS = {"running.interval_pace_tolerance_pct": 3, "running.interval_fast_flag_pct": 5}


def size(data) -> int:
    return len(json.dumps(data, indent=2, ensure_ascii=False).encode())


def trim(data: dict, keys: list[tuple[str, ...]]) -> dict:
    """Halve the given arrays (keeping the newest = last items) until the file fits."""
    data = json.loads(json.dumps(data))
    notes: list[str] = []
    while size(data) > MAX_BYTES:
        for path in keys:
            node = data
            for k in path[:-1]:
                node = node[k]
            arr = node[path[-1]]
            if len(arr) > 4:
                node[path[-1]] = arr[len(arr) // 2 :]
                notes.append(f"{'.'.join(path)} trimmed to the newest {len(node[path[-1]])}")
        if not notes:
            break
    if notes:
        data["_note"] = "example only: " + "; ".join(sorted(set(notes)))
    return data


def dump(name: str, data) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / name).write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {name} ({size(data) / 1000:.1f} KB)")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(BACKEND_DIR / "var" / "fitness.db"))
    ap.add_argument("--time", action="store_true", help="print cold / warm endpoint timings")
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

    with factory() as db:  # the copy gets the new pace keys as a config version, like the real DB
        cs.ensure_seeded(db, get_settings().engine_config_path)
        cs.create_version(db, PACE_KEYS, reason="Interval pace tolerance keys", author="user")
        db.commit()

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_api_token] = lambda: TOKEN
    client = TestClient(app)

    def get(path: str) -> dict:
        cache.invalidate()
        t0 = time.perf_counter()
        r = client.get(path, headers=AUTH)
        cold = (time.perf_counter() - t0) * 1000
        assert r.status_code == 200, (path, r.text)
        if args.time:
            t0 = time.perf_counter()
            client.get(path, headers=AUTH)
            print(
                f"  {path}: cold {cold:.0f} ms, cached {(time.perf_counter() - t0) * 1000:.0f} ms"
            )
        return r.json()

    with patch("apps.fitness.analytics.common.today", lambda: TODAY):
        lifts = get("/api/v1/stats/lifts")
        dump("stats_lifts.json", trim(lifts, [("lifts",)]))

        top = max(lifts["lifts"], key=lambda x: x["sessions"])  # longest history
        detail = get(f"/api/v1/stats/lifts/{top['variant_id']}")
        dump("stats_lift_detail.json", trim(detail, [("points",), ("prs",)]))

        dump("stats_volume_weekly.json", get("/api/v1/stats/volume/weekly?weeks=26"))
        running = get("/api/v1/stats/running?weeks=26")
        dump("stats_running.json", trim(running, [("weekly",), ("efficiency",)]))

        interval = running["intervals"][-1]["run_id"]
        easy = next(e for e in reversed(running["efficiency"]) if e["type"] == "easy")["run_id"]
        dump("stats_run_analysis_intervals.json", get(f"/api/v1/runs/{interval}/analysis"))
        dump("stats_run_analysis_easy.json", get(f"/api/v1/runs/{easy}/analysis"))

        dump("stats_health.json", trim(get("/api/v1/stats/health?days=90"), [("days",)]))
        dump(
            "stats_correlations.json",
            trim(
                get("/api/v1/stats/correlations?days=120"),
                [("items", i, "points") for i in range(5)],
            ),
        )
    app.dependency_overrides.clear()
    shutil.rmtree(tmp.parent, ignore_errors=True)


if __name__ == "__main__":
    main()
