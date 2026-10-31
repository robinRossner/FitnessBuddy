"""Adaptive flags and move markers on every plan item (GET /plan/week, /plan/today, sync pull),
the late-import healing of a make-up, and migration 0008."""

from __future__ import annotations

from datetime import date, timedelta

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text

from apps.fitness.db.models import PlannedSession
from apps.fitness.engine import data as D
from apps.fitness.engine import persist as P
from apps.fitness.engine import service as engine
from apps.fitness.settings import FITNESS_DIR
from tests.advisor_helpers import TODAY

NEXT = date(2026, 10, 26)
FIELDS = {"adaptive", "adapts_on", "moved_from", "change_reason", "status"}


def sessions(week: dict) -> list[dict]:
    return [s for d in week["days"] for s in d["sessions"]]


def test_every_plan_item_has_the_adaptive_fields(world, db, env, client):
    engine.run_engine(db, today=TODAY, settings=env)
    db.commit()
    week = client.get("/api/v1/plan/week?start=2026-10-26").json()
    items = sessions(week)
    assert len(items) == 7 and all(set(s) >= FIELDS for s in items)
    by = {s["kind"]: s for s in items}
    assert by["intervals"]["adaptive"] is True
    assert by["intervals"]["adapts_on"] == [
        "pace of your last quality session",
        "training phase (quality cycle)",
        "VDOT updates",
        "today's readiness",
        "weekly volume cap",
        "recovery after lower-body days",
    ]
    assert "HR drift of the last long run" in by["long_run"]["adapts_on"]
    assert "step-back weeks" in by["long_run"]["adapts_on"]
    assert by["easy_run"]["adapts_on"] == [
        "your recent easy-run paces",
        "today's readiness",
        "weekly and single-run volume caps",
    ]
    assert "your recent easy-run paces" in by["long_run"]["adapts_on"]
    assert by["lower"]["adaptive"] and "your reps at each load" in by["lower"]["adapts_on"]
    assert "stall ladder and deload weeks" in by["lower"]["adapts_on"]
    assert by["rest"]["adaptive"] is False and by["rest"]["adapts_on"] == []
    assert all(s["moved_from"] is None and s["change_reason"] is None for s in items)
    assert all(s["status"] == "proposed" for s in items)


def test_preview_items_and_today_carry_them_too(world, db, env, client):
    # nothing stored for next-next week: computed on the fly
    week = client.get("/api/v1/plan/week?start=2026-11-02").json()
    items = sessions(week)
    assert items and all(s["status"] == "preview" and set(s) >= FIELDS for s in items)
    assert next(s for s in items if s["kind"] == "intervals")["adaptive"] is True
    engine.run_engine(db, today=TODAY, settings=env)
    db.commit()
    today = client.get("/api/v1/plan/today").json()
    assert all(set(s) >= FIELDS for s in today["sessions"])
    assert today["sessions"][0]["kind"] == "long_run" and today["sessions"][0]["adaptive"] is True


def test_closed_sessions_are_not_adaptive(world, db, env, client):
    engine.run_engine(db, today=TODAY, settings=env)
    row = db.get(PlannedSession, D.planned_id(NEXT, "wed_easy_run"))
    row.status = "done"
    db.commit()
    items = sessions(client.get("/api/v1/plan/week?start=2026-10-26").json())
    wed = next(s for s in items if s["kind"] == "easy_run")
    assert wed["status"] == "done" and wed["adaptive"] is False and wed["adapts_on"] == []
    assert P.adaptive_fields("upper", "skipped") == {"adaptive": False, "adapts_on": []}


def test_sync_pull_planned_sessions_use_the_same_fields(world, db, env, client):
    engine.run_engine(db, today=TODAY, settings=env)
    db.commit()
    pulled = client.get("/api/v1/sync/pull", params={"limit": 5000}).json()
    planned = pulled["planned_sessions"]
    assert planned and all(set(p) >= FIELDS for p in planned)
    assert any(p["adaptive"] for p in planned)


def test_recovery_swap_of_the_engine_is_marked_as_moved(world, db, env, client):
    # a real lower day on Thursday: Friday's intervals are too soon after it (24 h rule)
    world.lift_session(NEXT + timedelta(days=3), "lower", [(world.squat, 80, [8, 8, 8])])
    db.commit()
    engine.run_engine(db, today=NEXT + timedelta(days=3), settings=env)
    db.commit()
    items = sessions(client.get("/api/v1/plan/week?start=2026-10-26").json())
    moved = [s for s in items if s["moved_from"]]
    assert moved and any(s["kind"] == "intervals" and "24 h" in s["change_reason"] for s in moved)


def test_a_late_import_cancels_the_makeup_and_the_template_returns(world, db, env):
    mon, tue, wed = NEXT, NEXT + timedelta(days=1), NEXT + timedelta(days=2)
    engine.run_engine(db, today=mon, settings=env)
    world.lift_session(mon, "upper", [(world.bench, 60, [8, 8, 8])])
    db.commit()
    engine.run_engine(db, today=wed, settings=env)
    slot = db.get(PlannedSession, D.planned_id(mon, "wed_easy_run"))
    assert slot.kind == "lower" and slot.moved_from == tue
    world.lift_session(tue, "lower", [(world.squat, 80, [8, 8, 8])])  # the export arrives late
    db.commit()
    res = engine.run_engine(db, today=wed, settings=env)
    db.refresh(slot)
    assert res.reconciled["done"] >= 1
    assert db.get(PlannedSession, D.planned_id(mon, "tue_lower")).status == "done"
    assert (slot.kind, slot.moved_from, slot.change_reason, slot.schedule_lock) == (
        "easy_run",
        None,
        None,
        None,
    )


# --------------------------------------------------------------------------- migration 0008


def test_migration_adds_the_schedule_section_as_a_derived_config_version(tmp_path):
    url = f"sqlite:///{tmp_path / 'm.db'}"
    cfg = Config(str(FITNESS_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(FITNESS_DIR / "alembic"))
    cfg.attributes["url"] = url
    command.upgrade(cfg, "0007")
    eng = create_engine(url)
    with eng.begin() as c:
        c.execute(
            text(
                "INSERT INTO config_versions (version, content, content_hash, is_active, "
                'created_at, author) VALUES (\'v3\', \'{"version": "v3", "approval_mode": '
                "\"manual\"}', 'h', 1, '2026-10-01 00:00:00', 'seed')"
            )
        )
    command.upgrade(cfg, "head")
    with eng.connect() as c:
        rows = c.execute(
            text(
                "SELECT version, is_active, author, content, parent_id "
                "FROM config_versions ORDER BY id"
            )
        ).all()
        cols = {r[1] for r in c.execute(text("PRAGMA table_info(planned_sessions)"))}
        tables = {
            r[0] for r in c.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))
        }
    # 0008 adds the schedule section (v3.1), 0009 the lifting section on top (v3.2), 0010 the
    # progression v2 keys (v3.3), 0011 the volume-hold keys (v3.4), 0013 the easy pace
    # calibration keys (v3.5), 0014 the VDOT-from-interval-reps keys (v3.6, active)
    assert [(r[0], r[1], r[2]) for r in rows] == [
        ("v3", 0, "seed"),
        ("v3.1", 0, "engine"),
        ("v3.2", 0, "engine"),
        ("v3.3", 0, "engine"),
        ("v3.4", 0, "engine"),
        ("v3.5", 0, "engine"),
        ("v3.6", 1, "engine"),
    ]
    import json

    def load(raw):
        return json.loads(raw) if isinstance(raw, str) else raw

    content = load(rows[1][3])
    assert content["version"] == "v3.1" and content["schedule"]["makeup_priority"][0] == "lower"
    assert content["schedule"]["max_sessions_per_day"] == 2 and rows[1][4] == 1
    latest = load(rows[2][3])
    assert latest["schedule"] == content["schedule"] and latest["lifting"] == {
        "rest_default_s": 120
    }
    assert rows[2][4] == rows[1][4] + 1
    assert {"moved_from", "change_reason", "changed_by", "schedule_lock"} <= cols
    assert "advisor_schedule_changes" in tables
    command.downgrade(cfg, "0007")
    command.upgrade(cfg, "head")  # idempotent for the config: the section is already there
    with eng.connect() as c:
        assert c.execute(text("SELECT count(*) FROM config_versions")).scalar() == 7
    eng.dispose()
