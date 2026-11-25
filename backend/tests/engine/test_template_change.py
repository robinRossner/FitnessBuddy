"""A changed weekly template re-plans cleanly: stale open rows are soft-deleted (tombstones the app
picks up on pull), past days are never backfilled, done rows stay, and no make-ups are triggered."""

from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import select

from apps.fitness.db.models import AdvisorTrigger, PlannedSession
from apps.fitness.engine.core.ids import planned_id
from apps.fitness.engine.store import config_service as cs
from apps.fitness.engine.store import service
from tests.support.world import easy_laps

MON = date(2026, 11, 23)
WED = date(2026, 11, 25)
NEXT_MON = MON + timedelta(days=7)
OLD = {
    "mon": "upper", "tue": "lower", "wed": "easy_run", "thu": "upper",
    "fri": "intervals", "sat": "rest", "sun": "long_run",
}  # fmt: skip
NEW = {
    "mon": "lower", "tue": "easy_run", "wed": "upper", "thu": "intervals",
    "fri": "rest", "sat": "long_run", "sun": "upper",
}  # fmt: skip


def live(db, week: date) -> dict[str, PlannedSession]:
    rows = db.scalars(
        select(PlannedSession).where(
            PlannedSession.day >= week,
            PlannedSession.day < week + timedelta(days=7),
            PlannedSession.deleted_at.is_(None),
        )
    )
    return {r.id: r for r in rows}


def expect(week: date, template: dict[str, str]) -> dict[str, str]:
    days = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
    return {planned_id(week, f"{d}_{template[d]}"): template[d] for d in days}


def set_template(db, template):
    cs.create_version(db, {"weekly_template": template}, reason="new split", author="user")
    db.commit()


def test_template_change_mid_week_replans_cleanly(world, db, env):
    running_base(world)
    set_template(db, OLD)
    service.run_engine(db, today=MON, settings=env)
    for slot in ("mon_upper", "tue_lower"):
        db.get(PlannedSession, planned_id(MON, slot)).status = "done"
    db.commit()
    old_future = [
        planned_id(MON, s)
        for s in ("wed_easy_run", "thu_upper", "fri_intervals", "sat_rest", "sun_long_run")
    ] + list(live(db, NEXT_MON))
    assert len(old_future) == 12

    set_template(db, NEW)
    res = service.run_engine(db, today=WED, settings=env)

    this_week = live(db, MON)
    # done rows + new future slots; no new rows on the past days (mon_lower, tue_easy_run)
    want = {
        planned_id(MON, "mon_upper"): "upper",
        planned_id(MON, "tue_lower"): "lower",
        planned_id(MON, "wed_upper"): "upper",
        planned_id(MON, "thu_intervals"): "intervals",
        planned_id(MON, "fri_rest"): "rest",
        planned_id(MON, "sat_long_run"): "long_run",
        planned_id(MON, "sun_upper"): "upper",
    }
    assert {k: r.kind.value for k, r in this_week.items()} == want
    assert {r.status for k, r in this_week.items() if k in list(want)[:2]} == {"done"}
    assert {r.status for k, r in this_week.items() if k in list(want)[2:]} == {"proposed"}
    assert db.get(PlannedSession, planned_id(MON, "mon_lower")) is None
    assert db.get(PlannedSession, planned_id(MON, "tue_easy_run")) is None

    # stale rows are tombstones, not hard deletes
    for pid in old_future:
        r = db.get(PlannedSession, pid)
        assert r.deleted_at is not None and r.changed_by == "engine"
        assert r.change_reason == "weekly template changed"
    assert sum(w.persisted.retired for w in res.weeks) == 12

    nxt = live(db, NEXT_MON)
    assert {k: r.kind.value for k, r in nxt.items()} == expect(NEXT_MON, NEW)
    assert {r.status for r in nxt.values()} == {"proposed"}

    # no missed sessions, no make-ups, no triggers
    assert res.reconciled["skipped"] == 0 and not res.makeups.results
    assert not list(
        db.scalars(select(AdvisorTrigger).where(AdvisorTrigger.type == "missed_session"))
    )

    # idempotent
    again = service.run_engine(db, today=WED, settings=env)
    assert sum(w.persisted.retired + w.persisted.created for w in again.weeks) == 0


def running_base(world):
    """Recent runs, so that the intervals slot stays intervals (no 'first week back' downgrade)."""
    for d, km in ((date(2026, 11, 14), 8), (date(2026, 11, 18), 6), (date(2026, 11, 21), 10)):
        world.run(d, easy_laps(km))


def test_retire_respects_only_days_and_closed_rows(world, db, env):
    set_template(db, OLD)
    service.run_engine(db, today=MON, settings=env)
    thu = db.get(PlannedSession, planned_id(MON, "thu_upper"))
    thu.status = "modified"
    db.commit()
    set_template(db, NEW)
    service.apply_week(db, today=WED, week_start=MON, settings=env, only_days={date(2026, 11, 27)})
    db.commit()
    assert db.get(PlannedSession, planned_id(MON, "fri_intervals")).deleted_at is not None
    assert (
        db.get(PlannedSession, planned_id(MON, "wed_easy_run")).deleted_at is None
    )  # not in only_days
    assert db.get(PlannedSession, planned_id(MON, "thu_upper")).deleted_at is None  # modified


def test_template_going_back_revives_retired_rows(world, db, env):
    running_base(world)
    set_template(db, OLD)
    service.run_engine(db, today=MON, settings=env)
    db.commit()
    set_template(db, NEW)
    service.run_engine(db, today=MON, settings=env)
    set_template(db, OLD)
    service.run_engine(db, today=MON, settings=env)
    db.commit()
    assert {k: r.kind.value for k, r in live(db, NEXT_MON).items()} == expect(NEXT_MON, OLD)
