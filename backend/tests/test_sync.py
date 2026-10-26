"""Sync + catalog API: push (LWW), pull (cursor, tombstones, snapshot window), history, health."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text

from app.api.auth import get_api_token
from app.api.routes import get_db
from app.db.models import (
    BodyMeasurement,
    ExerciseSessionStats,
    ExerciseVariant,
    PlannedSession,
    TrainingSession,
    WorkoutSet,
)
from app.db.types import iso_z
from app.importers.strong.importer import run_import
from app.main import app
from app.settings import get_settings

TOKEN = "test-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
NOW = datetime.now(UTC).replace(microsecond=0)


def ts(dt: datetime) -> str:
    return iso_z(dt)


def uid() -> str:
    return str(uuid.uuid4())


@pytest.fixture
def engine_hook(monkeypatch) -> MagicMock:
    """The engine re-run is mocked everywhere (it would otherwise open the real database)."""
    hook = MagicMock()
    monkeypatch.setattr("app.api.sync.rerun_engine", hook)
    return hook


@pytest.fixture
def client(db, options, engine_hook):
    run_import(db, options)  # catalog + 4 old sessions (2024)
    db.commit()
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_api_token] = lambda: TOKEN
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture
def squat_id(db) -> int:
    return db.scalar(select(ExerciseVariant.id).where(ExerciseVariant.name == "Squat (Barbell)"))


def session(id_=None, *, started=None, updated=None, **kw) -> dict:
    started = started or NOW - timedelta(hours=2)
    rec = {
        "id": id_ or uid(),
        "planned_session_id": None,
        "gym_id": None,
        "kind": "lower",
        "name": "Lower",
        "started_at": ts(started),
        "ended_at": ts(started + timedelta(hours=1)),
        "notes": None,
        "updated_at": ts(updated or NOW),
        "deleted_at": None,
    }
    return {**rec, **kw}


def wset(session_id, variant_id, *, id_=None, order=1, updated=None, **kw) -> dict:
    rec = {
        "id": id_ or uid(),
        "session_id": session_id,
        "variant_id": variant_id,
        "exercise_order": 0,
        "set_order": order,
        "set_type": "working",
        "weight_kg": 100.0,
        "reps": 5,
        "duration_s": None,
        "completed_at": ts(NOW),
        "notes": None,
        "updated_at": ts(updated or NOW),
        "deleted_at": None,
    }
    return {**rec, **kw}


def weigh(id_=None, *, kg=82.5, updated=None, **kw) -> dict:
    rec = {
        "id": id_ or uid(),
        "measured_at": ts(NOW),
        "weight_kg": kg,
        "note": None,
        "updated_at": ts(updated or NOW),
        "deleted_at": None,
    }
    return {**rec, **kw}


def push(client, *, sessions=(), sets=(), body=(), device="iphone-1"):
    r = client.post(
        "/api/v1/sync/push",
        headers=AUTH,
        json={
            "device_id": device,
            "sessions": list(sessions),
            "sets": list(sets),
            "body_measurements": list(body),
        },
    )
    assert r.status_code == 200, r.text
    return r.json()


def statuses(resp) -> list[str]:
    return [x["status"] for x in resp["results"]]


def pull(client, since="", **params):
    r = client.get("/api/v1/sync/pull", headers=AUTH, params={"since": since, **params})
    assert r.status_code == 200, r.text
    return r.json()


def pull_all(client, since="", limit=2000):
    """Follow has_more; returns (merged records by key, final cursor, pages)."""
    merged: dict[str, list] = {}
    pages = 0
    while True:
        page = pull(client, since, limit=limit)
        pages += 1
        for k, v in page.items():
            if isinstance(v, list):
                merged.setdefault(k, []).extend(v)
        since = page["cursor"]
        if not page["has_more"]:
            return merged, since, pages


# --------------------------------------------------------------------------- auth + health


def test_auth_required_everywhere_but_health(client):
    for method, url in [
        ("post", "/api/v1/sync/push"),
        ("get", "/api/v1/sync/pull"),
        ("get", "/api/v1/variants/1/history"),
    ]:
        assert getattr(client, method)(url).status_code == 401
        bad = {"Authorization": "Bearer nope"}
        assert getattr(client, method)(url, headers=bad).status_code == 401
    r = client.get("/api/v1/health")  # no auth
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "version": "0.1.0"}


# --------------------------------------------------------------------------- push


def test_push_creates_everything_and_pull_returns_it(client, db, squat_id):
    s = session()
    sets = [wset(s["id"], squat_id, order=i) for i in (1, 2)]
    m = weigh()
    resp = push(client, sessions=[s], sets=sets, body=[m])
    assert resp["server_time"].endswith("Z")
    assert [(r["entity"], r["status"], r["reason"]) for r in resp["results"]] == [
        ("session", "applied", None),
        ("set", "applied", None),
        ("set", "applied", None),
        ("body_measurement", "applied", None),
    ]
    assert [r["id"] for r in resp["results"]] == [s["id"], sets[0]["id"], sets[1]["id"], m["id"]]

    row = db.get(TrainingSession, s["id"])
    assert row.source == "app" and row.source_ref == "iphone-1" and row.duration_s == 3600
    assert db.get(BodyMeasurement, m["id"]).weight_kg == 82.5

    snap, _, _ = pull_all(client)
    got = next(x for x in snap["sessions"] if x["id"] == s["id"])
    assert got == {**s, "ended_at": s["ended_at"]}  # exact round trip of the contract fields
    assert next(x for x in snap["sets"] if x["id"] == sets[1]["id"]) == sets[1]
    assert next(x for x in snap["body_measurements"] if x["id"] == m["id"]) == m


def test_lww_newer_wins_older_and_equal_are_stale(client, db):
    s = session(name="v1", updated=NOW)
    assert statuses(push(client, sessions=[s])) == ["applied"]

    newer = {**s, "name": "v2", "updated_at": ts(NOW + timedelta(seconds=5))}
    older = {**s, "name": "v0", "updated_at": ts(NOW - timedelta(seconds=5))}
    equal = {**s, "name": "equal"}
    assert statuses(push(client, sessions=[older])) == ["stale"]
    assert statuses(push(client, sessions=[equal])) == ["stale"]
    assert db.get(TrainingSession, s["id"]).name == "v1"
    assert statuses(push(client, sessions=[newer])) == ["applied"]
    db.expire_all()
    assert db.get(TrainingSession, s["id"]).name == "v2"
    assert statuses(push(client, sessions=[older])) == ["stale"]  # still stale after v2


def test_lww_applies_to_sets_and_measurements(client, db, squat_id):
    s = session()
    st = wset(s["id"], squat_id, reps=5)
    m = weigh(kg=82.0)
    push(client, sessions=[s], sets=[st], body=[m])
    st2 = {**st, "reps": 6, "updated_at": ts(NOW + timedelta(seconds=1))}
    m_old = {**m, "weight_kg": 80.0, "updated_at": ts(NOW - timedelta(seconds=1))}
    resp = push(client, sets=[st2], body=[m_old])
    assert statuses(resp) == ["applied", "stale"]
    db.expire_all()
    assert db.get(WorkoutSet, st["id"]).reps == 6
    assert db.get(BodyMeasurement, m["id"]).weight_kg == 82.0


def test_repush_is_idempotent(client, db, squat_id, engine_hook):
    s = session()
    sets = [wset(s["id"], squat_id, order=i) for i in (1, 2, 3)]
    m = weigh()
    payload = {"sessions": [s], "sets": sets, "body": [m]}
    push(client, **payload)

    def snapshot():
        db.expire_all()
        return (
            db.execute(
                text("select id, updated_at, server_seq from sets where session_id=:s order by id"),
                {"s": s["id"]},
            ).all(),
            db.execute(
                text("select updated_at, server_seq from sessions where id=:s"), {"s": s["id"]}
            ).all(),
            db.scalar(select(func.max(text("seq"))).select_from(text("sync_seq"))),
            db.scalar(select(func.count()).select_from(WorkoutSet)),
        )

    before = snapshot()
    engine_hook.reset_mock()
    again = push(client, **payload)
    assert set(statuses(again)) == {"stale"}
    assert snapshot() == before  # nothing changed, not even the change sequence
    engine_hook.assert_not_called()


def test_rejections_do_not_fail_the_request(client, db, squat_id):
    s = session()
    ghost = uid()
    good = wset(s["id"], squat_id)
    resp = push(
        client,
        sessions=[s, session(kind="push"), {"id": "not-a-uuid"}, session(gym_id=9999)],
        sets=[
            wset(ghost, squat_id),  # unknown session
            wset(s["id"], 987654),  # unknown variant
            wset(s["id"], squat_id, weight_kg=-3),  # invalid value
            good,
        ],
        body=[weigh(kg=5)],
    )
    r = resp["results"]
    assert statuses(resp) == [
        "applied",
        "rejected",
        "rejected",
        "rejected",
        "rejected",
        "rejected",
        "rejected",
        "applied",
        "rejected",
    ]
    assert "unknown session_id" in r[4]["reason"] and r[4]["id"] is not None
    assert "unknown variant_id 987654" in r[5]["reason"]
    assert "weight_kg" in r[6]["reason"]
    assert "unknown gym_id" in r[3]["reason"]
    assert r[2]["id"] == "not-a-uuid" and r[2]["reason"].startswith("invalid")
    assert db.get(WorkoutSet, ghost) is None


def test_sets_may_come_with_their_session_and_unknown_session_is_rejected(client, squat_id):
    sid = uid()
    st = wset(sid, squat_id)
    # session unknown anywhere -> rejected; same set once its session is in the payload -> applied
    assert statuses(push(client, sets=[st])) == ["rejected"]
    assert statuses(push(client, sessions=[session(sid)], sets=[st])) == ["applied", "applied"]


def test_uppercase_uuids_are_normalised(client, db):
    s = session()
    up = {**s, "id": s["id"].upper()}
    resp = push(client, sessions=[up])
    assert resp["results"][0]["id"] == s["id"] and statuses(resp) == ["applied"]
    assert db.get(TrainingSession, s["id"]) is not None


def test_unknown_planned_session_id_is_dropped_not_rejected(client, db):
    s = session(planned_session_id=uid())
    assert statuses(push(client, sessions=[s])) == ["applied"]
    assert db.get(TrainingSession, s["id"]).planned_session_id is None


# --------------------------------------------------------------------------- planned sessions


def _plan(db, kind="lower", status="proposed") -> str:
    pid = uid()
    db.add(
        PlannedSession(
            id=pid,
            day=NOW.date(),
            kind=kind,
            status=status,
            prescription={"title": "Lower", "session": {"type": "lifting", "exercises": []}},
        )
    )
    db.commit()
    return pid


def test_push_marks_planned_done_or_modified_and_reverts_on_delete(client, db, engine_hook):
    p_done, p_mod = _plan(db, "lower"), _plan(db, "upper")
    s1 = session(planned_session_id=p_done, kind="lower")
    s2 = session(planned_session_id=p_mod, kind="other")
    push(client, sessions=[s1, s2])
    db.expire_all()
    assert db.get(PlannedSession, p_done).status == "done"
    assert db.get(PlannedSession, p_mod).status == "modified"
    assert db.get(TrainingSession, s1["id"]).planned_session_id == p_done

    gone = {
        **s1,
        "deleted_at": ts(NOW + timedelta(seconds=9)),
        "updated_at": ts(NOW + timedelta(seconds=9)),
    }
    push(client, sessions=[gone])
    db.expire_all()
    assert db.get(PlannedSession, p_done).status == "proposed"
    assert db.get(PlannedSession, p_mod).status == "modified"  # untouched


def test_planned_status_change_reaches_pull(client, db):
    p = _plan(db)
    cur = pull_all(client)[1]
    push(client, sessions=[session(planned_session_id=p)])
    page, _, _ = pull_all(client, cur)
    plan = next(x for x in page["planned_sessions"] if x["id"] == p)
    assert plan["status"] == "done" and plan["date"] == NOW.date().isoformat()
    assert plan["updated_at"].endswith("Z") and plan["session"]["type"] == "lifting"


def test_engine_rerun_triggered_only_by_applied_training_data(client, squat_id, engine_hook):
    s = session()
    push(client, sessions=[s], sets=[wset(s["id"], squat_id)])
    engine_hook.assert_called_once_with("sync push")
    engine_hook.reset_mock()
    push(client, body=[weigh()])  # weigh-in only
    push(client, sessions=[s])  # stale
    push(client, sets=[wset(uid(), squat_id)])  # rejected
    engine_hook.assert_not_called()


def test_rerun_engine_never_raises(monkeypatch):
    from app.jobs import engine_jobs

    class _Boom:
        def __enter__(self):
            raise RuntimeError("db exploded")

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(engine_jobs, "session_scope", lambda: _Boom())
    engine_jobs.rerun_engine("test")  # logs, does not raise


# --------------------------------------------------------------------------- stats + history


def test_push_rebuilds_stats_and_history_endpoint(client, db, squat_id):
    older = session(started=NOW - timedelta(days=7), updated=NOW - timedelta(days=7))
    newer = session(started=NOW - timedelta(days=1))
    push(
        client,
        sessions=[older, newer],
        sets=[
            wset(older["id"], squat_id, weight_kg=100, reps=5, order=1),
            wset(newer["id"], squat_id, weight_kg=110, reps=5, order=1),
            wset(newer["id"], squat_id, weight_kg=60, reps=10, order=0, set_type="warmup"),
        ],
    )
    stats = {
        r.session_id: r
        for r in db.scalars(
            select(ExerciseSessionStats).where(ExerciseSessionStats.variant_id == squat_id)
        )
    }
    assert stats[newer["id"]].top_set_weight_kg == 110 and stats[newer["id"]].is_weight_pr
    h = client.get(f"/api/v1/variants/{squat_id}/history", headers=AUTH).json()
    assert [x["session_id"] for x in h][:2] == [newer["id"], older["id"]]  # newest first
    assert h[0]["sets"][0] == {  # ordered by set order; warmup flagged
        "weight_kg": 60.0,
        "reps": 10,
        "duration_s": None,
        "set_type": "warmup",
    }
    assert h[0]["best_e1rm"] == pytest.approx(110 * (1 + 5 / 30), abs=0.01)
    local = datetime.fromisoformat(newer["started_at"]).astimezone(ZoneInfo(get_settings().user_tz))
    assert h[0]["date"] == local.date().isoformat()
    assert len(client.get(f"/api/v1/variants/{squat_id}/history?limit=1", headers=AUTH).json()) == 1

    # tombstoned sessions drop out of history and stats
    gone = {
        **newer,
        "deleted_at": ts(NOW + timedelta(seconds=3)),
        "updated_at": ts(NOW + timedelta(seconds=3)),
    }
    push(client, sessions=[gone])
    h2 = client.get(f"/api/v1/variants/{squat_id}/history", headers=AUTH).json()
    assert newer["id"] not in [x["session_id"] for x in h2]
    assert (
        db.scalar(
            select(func.count())
            .select_from(ExerciseSessionStats)
            .where(ExerciseSessionStats.session_id == newer["id"])
        )
        == 0
    )


def test_history_unknown_variant_404(client):
    assert client.get("/api/v1/variants/424242/history", headers=AUTH).status_code == 404
    assert client.get("/api/v1/variants/1/history?limit=0", headers=AUTH).status_code == 422


# --------------------------------------------------------------------------- pull


def test_pull_shapes_and_catalog(client, db):
    page = pull(client)
    assert set(page) >= {
        "cursor", "has_more", "sessions", "sets", "body_measurements",
        "planned_sessions", "variants", "exercises", "gyms", "config",
    }  # fmt: skip
    assert len(page["variants"]) == db.scalar(select(func.count()).select_from(ExerciseVariant))
    v = page["variants"][0]
    assert set(v) == {
        "id", "exercise_id", "name", "gym_id", "equipment_type", "load_convention",
        "active", "deleted_at", "weight_ladder", "rep_range", "increment_kg",
    }  # fmt: skip
    assert set(page["exercises"][0]) == {
        "id", "name", "muscle_group", "movement_pattern", "body_region", "default_equipment",
        "is_timed",
    }  # fmt: skip
    assert {"id", "name"} <= set(page["gyms"][0])
    cfg = page["config"]
    assert isinstance(cfg["version"], str) and cfg["dumbbell_increment_kg"] in (2.0, 2.5)
    assert len(cfg["rep_range"]) == 2
    # old (2024) imported sessions are outside the 120-day snapshot window
    assert page["sessions"] == [] and page["sets"] == []


def test_snapshot_limits_history_to_120_days_but_incremental_does_not(client, db, squat_id):
    recent = session(started=NOW - timedelta(days=30))
    old = session(started=NOW - timedelta(days=200))
    push(
        client,
        sessions=[recent, old],
        sets=[wset(recent["id"], squat_id), wset(old["id"], squat_id)],
    )
    snap, cur, _ = pull_all(client)
    assert {x["id"] for x in snap["sessions"]} == {recent["id"]}
    assert {x["session_id"] for x in snap["sets"]} == {recent["id"]}
    assert len(snap["variants"]) == db.scalar(select(func.count()).select_from(ExerciseVariant))

    # nothing leaks later: old rows written before the snapshot do not show up as changes ...
    again = pull(client, cur)
    assert again["sessions"] == again["sets"] == [] and again["cursor"] == cur
    # ... but an actual change to an old session does
    edit = {**old, "name": "edited", "updated_at": ts(NOW + timedelta(seconds=2))}
    push(client, sessions=[edit])
    changed = pull(client, cur)
    assert [x["id"] for x in changed["sessions"]] == [old["id"]]


def test_snapshot_filter_survives_paging(client, squat_id):
    recent = [session(started=NOW - timedelta(days=d)) for d in (1, 2, 3)]
    old = [session(started=NOW - timedelta(days=300 + d)) for d in range(3)]
    push(client, sessions=recent + old)
    snap, cur, pages = pull_all(client, limit=1)
    assert pages > 3
    assert {x["id"] for x in snap["sessions"]} == {s["id"] for s in recent}
    assert pull(client, cur)["sessions"] == []


def test_cursor_pagination_and_incremental_changes(client, db, squat_id):
    s = session()
    sets = [wset(s["id"], squat_id, order=i) for i in range(1, 6)]
    push(client, sessions=[s], sets=sets)

    first = pull(client, "", limit=3)
    assert first["has_more"] is True
    seen = {k: len(v) for k, v in first.items() if isinstance(v, list) and k != "variants"}
    pages = 1
    cursor, more = first["cursor"], first["has_more"]
    ids = [x["id"] for x in first["sets"]]
    while more:
        nxt = pull(client, cursor, limit=3)
        pages += 1
        ids += [x["id"] for x in nxt["sets"]]
        cursor, more = nxt["cursor"], nxt["has_more"]
    assert sorted(ids) == sorted(x["id"] for x in sets)  # every set exactly once
    assert len(ids) == len(set(ids)) and pages >= 3 and seen

    quiet = pull(client, cursor)
    assert quiet["has_more"] is False and quiet["cursor"] == cursor
    assert all(
        quiet[k] == [] for k in ("sessions", "sets", "body_measurements", "planned_sessions")
    )

    edit = {**sets[0], "reps": 9, "updated_at": ts(NOW + timedelta(seconds=4))}
    push(client, sets=[edit])
    delta = pull(client, cursor)
    assert [x["id"] for x in delta["sets"]] == [sets[0]["id"]] and delta["sets"][0]["reps"] == 9
    assert delta["sessions"] == [] and delta["cursor"] != cursor
    assert pull(client, delta["cursor"])["sets"] == []


def test_tombstones_round_trip_via_pull(client, squat_id):
    s = session()
    st = wset(s["id"], squat_id)
    m = weigh()
    push(client, sessions=[s], sets=[st], body=[m])
    cursor = pull_all(client)[1]
    t = ts(NOW + timedelta(seconds=10))
    push(
        client,
        sessions=[{**s, "deleted_at": t, "updated_at": t}],
        sets=[{**st, "deleted_at": t, "updated_at": t}],
        body=[{**m, "deleted_at": t, "updated_at": t}],
    )
    delta = pull(client, cursor)
    assert delta["sessions"][0]["deleted_at"] == t
    assert delta["sets"][0]["deleted_at"] == t
    assert delta["body_measurements"][0]["deleted_at"] == t
    # a snapshot still lists them (as tombstones) so a client can reconcile
    snap, _, _ = pull_all(client)
    assert next(x for x in snap["sets"] if x["id"] == st["id"])["deleted_at"] == t
    # LWW also resurrects: a newer record with deleted_at = null wins
    back = {**s, "updated_at": ts(NOW + timedelta(seconds=20))}
    assert statuses(push(client, sessions=[back])) == ["applied"]
    assert pull(client, delta["cursor"])["sessions"][0]["deleted_at"] is None


def test_bad_cursor_is_400(client):
    r = client.get("/api/v1/sync/pull", headers=AUTH, params={"since": "garbage"})
    assert r.status_code == 400


def test_old_session_kinds_map_to_app_kinds(client, db):
    s = session()
    push(client, sessions=[s])
    row = db.get(TrainingSession, s["id"])
    for stored, expected in [("push", "upper"), ("pull", "upper"), ("legs", "lower"),
                             ("calisthenics", "other"), ("full", "full")]:  # fmt: skip
        row.session_kind = stored
        db.commit()
        got = next(x for x in pull(client, "", limit=2000)["sessions"] if x["id"] == s["id"])
        assert got["kind"] == expected


# --------------------------------------------------------------------------- change sequence


def test_every_write_path_stamps_server_seq_and_e1rm_rebuilds_do_not(client, db, squat_id):
    # importer-written rows (ORM) have a sequence number
    assert (
        db.scalar(
            select(func.count())
            .select_from(TrainingSession)
            .where(TrainingSession.server_seq.is_(None))
        )
        == 0
    )
    assert db.scalar(select(func.count()).select_from(ExerciseVariant).where(
        ExerciseVariant.server_seq.is_(None))) == 0  # fmt: skip

    s = session()
    st = wset(s["id"], squat_id)
    push(client, sessions=[s], sets=[st])
    db.expire_all()
    seq = db.get(WorkoutSet, st["id"]).server_seq
    updated = db.get(WorkoutSet, st["id"]).updated_at

    # a derived-only change (e1rm_kg) neither bumps server_seq nor updated_at
    db.execute(text("update sets set e1rm_kg = 1.23 where id = :i"), {"i": st["id"]})
    db.commit()
    db.expire_all()
    row = db.get(WorkoutSet, st["id"])
    assert row.server_seq == seq and row.updated_at == updated
    # while a real edit through any path (raw SQL here) gets a new, larger sequence number
    db.execute(text("update sets set reps = 12 where id = :i"), {"i": st["id"]})
    db.commit()
    db.expire_all()
    assert db.get(WorkoutSet, st["id"]).server_seq > seq

    # a server-side ORM edit bumps updated_at and the sequence: clients see it as a change
    sess = db.get(TrainingSession, s["id"])
    before = sess.server_seq
    sess.notes = "server edit"
    db.commit()
    db.expire_all()
    assert db.get(TrainingSession, s["id"]).server_seq > before


def test_stats_rebuild_keeps_client_visible_state(client, db, squat_id):
    from app.stats import rebuild_with_config

    s = session()
    st = wset(s["id"], squat_id)
    push(client, sessions=[s], sets=[st])
    db.execute(text("update sets set e1rm_kg = null where id = :i"), {"i": st["id"]})
    db.commit()
    db.expire_all()
    before = db.get(WorkoutSet, st["id"])
    seq, updated = before.server_seq, before.updated_at
    assert before.e1rm_kg is None

    rebuild_with_config(db)  # full rebuild recomputes e1rm_kg ...
    db.commit()
    db.expire_all()
    after = db.get(WorkoutSet, st["id"])
    assert after.e1rm_kg is not None
    assert after.updated_at == updated and after.server_seq == seq  # ... but is not a sync change


def test_partial_stats_rebuild_leaves_other_variants_alone(client, db, squat_id):
    from app.stats import rebuild_with_config

    other = db.scalar(select(ExerciseVariant.id).where(ExerciseVariant.id != squat_id))
    n_total = db.scalar(select(func.count()).select_from(ExerciseSessionStats))
    n_other = db.scalar(
        select(func.count())
        .select_from(ExerciseSessionStats)
        .where(ExerciseSessionStats.variant_id != squat_id)
    )
    rebuild_with_config(db, [squat_id])
    assert db.scalar(select(func.count()).select_from(ExerciseSessionStats)) == n_total
    assert (
        db.scalar(
            select(func.count())
            .select_from(ExerciseSessionStats)
            .where(ExerciseSessionStats.variant_id != squat_id)
        )
        == n_other
    )
    assert other is not None


def test_tombstoned_variant_reaches_pull_and_leaves_the_variant_list(client, db, squat_id):
    cur = pull_all(client)[1]
    variants = {v["id"]: v for v in pull(client)["variants"]}
    assert all(v["deleted_at"] is None for v in variants.values())
    other = next(i for i in variants if i != squat_id)
    row = db.get(ExerciseVariant, other)
    row.deleted_at = datetime.now(UTC)
    db.commit()
    delta = pull(client, cur)
    assert [v["id"] for v in delta["variants"]] == [other]
    assert delta["variants"][0]["deleted_at"].endswith("Z")
    listed = client.get("/api/v1/variants", headers=AUTH).json()
    assert other not in [v["id"] for v in listed]
