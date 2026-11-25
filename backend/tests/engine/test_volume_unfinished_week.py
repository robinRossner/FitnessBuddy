"""The weekly volume cap never counts an unfinished week as its partial actual km: it counts the
projected km (run so far + still-open planned runs), or leaves the week out without a plan."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from apps.fitness.db.models import PlannedSession
from apps.fitness.engine.core import running as R
from apps.fitness.engine.core.planner import generate_week
from apps.fitness.engine.store import data as D
from tests.engine.test_engine_planner import inputs, lap, run_from

TODAY = date(2031, 3, 12)  # Wednesday
NEXT = date(2031, 3, 17)
CUR = date(2031, 3, 10)


def km_run(day: date, km: float):
    half = km * 500
    return run_from(day, [lap(330, 148, dist=half), lap(330, 148, dist=half)])


def history():
    """Three complete weeks of 22.4 / 18.9 / 17.5 km (long runs 9.4 km); then nothing yet."""
    out = []
    for mon, kms in (
        (date(2031, 2, 17), (5.0, 8.0, 9.4)),
        (date(2031, 2, 24), (3.5, 6.0, 9.4)),
        (date(2031, 3, 3), (3.0, 5.1, 9.4)),
    ):
        for off, km in zip((1, 3, 5), kms, strict=True):
            out.append(km_run(mon + timedelta(days=off), km))
    return out


def plan_next(**kw):
    return generate_week(inputs(today=TODAY, week_start=NEXT, runs=history(), **kw))


def test_weekly_totals_of_the_fixture():
    w = R.weekly_km(history())
    assert [round(w[d], 1) for d in sorted(w)] == [22.4, 18.9, 17.5]
    assert CUR not in w


def test_unfinished_week_counts_as_projected_km():
    plan = plan_next(open_run_km={CUR: 17.3})
    v = plan.volume
    mean = (sum(R.weekly_km(history()).values()) + 17.3) / 4
    assert v["mean_km_4w"] == pytest.approx(mean, abs=0.01)
    assert v["cap_km"] == pytest.approx(max(mean * 1.1, mean + 2), abs=0.01)
    assert v["projected_weeks"] == {"2031-03-10": {"run_km": 0.0, "planned_km": 17.3}}
    assert "projected 17.3 km (0 km run + 17.3 km still planned)" in v["reasoning"]
    assert v["hold"]["last_week_km"] == pytest.approx(17.5)  # the last COMPLETED week
    assert v["cap_km"] == pytest.approx(21.02, abs=0.015)
    assert v["planned_km"]["long_run"] == 10.0  # not cut by the weekly cap
    assert not any("long_run" in n for n in v["notes"])
    assert v["planned_km"]["long_run"] == v["planned_km_before_cap"]["long_run"]


def test_without_plan_info_the_unfinished_week_is_left_out():
    v = plan_next().volume  # open_run_km None
    weekly = R.weekly_km(history())
    older = date(2031, 2, 17) - timedelta(days=7)
    mean = (sum(weekly.values()) + weekly.get(older, 0.0)) / 4
    assert v["mean_km_4w"] == pytest.approx(mean, abs=0.01)
    assert v["left_out_weeks"] == ["2031-03-10"]
    assert "left out of the mean" in v["reasoning"]


def test_completed_weeks_are_unchanged_when_planning_the_current_week():
    vh = R.volume_history(history(), TODAY, CUR, {CUR: 5.0})
    assert vh.weekly == R.weekly_km(history()) and not vh.skip and not vh.unfinished


def test_partly_run_week_adds_the_runs_so_far():
    runs = [*history(), km_run(date(2031, 3, 11), 5.0)]
    vh = R.volume_history(runs, TODAY, NEXT, {CUR: 12.0})
    assert vh.weekly[CUR] == pytest.approx(17.0) and vh.projected[CUR] == (5.0, 12.0)


def test_hold_passes_over_an_unfinished_last_week():
    vh = R.volume_history(history(), TODAY, NEXT, {CUR: 17.3})
    hold = R.hold_volume(
        vh.weekly,
        history(),
        NEXT,
        limit_factor=1.5,
        flagged=lambda r: False,
        unfinished=vh.unfinished,
    )
    assert hold is not None and hold.last_week_km == pytest.approx(17.5)


def test_loader_sums_the_open_planned_runs_of_unfinished_weeks(db):
    def add(day, kind, status, km, deleted=None):
        db.add(
            PlannedSession(
                day=day,
                kind=kind,
                status=status,
                prescription={"session": {"distance_km": km}},
                deleted_at=deleted,
            )
        )

    add(date(2031, 3, 11), "easy_run", "done", 3.0)  # already run
    add(date(2031, 3, 12), "intervals", "proposed", 7.4)
    add(date(2031, 3, 13), "upper", "proposed", None)  # not a run
    add(date(2031, 3, 14), "easy_run", "skipped", 4.0)  # closed
    add(date(2031, 3, 15), "long_run", "approved", 9.9)
    add(date(2031, 3, 16), "easy_run", "proposed", 5.0, deleted=datetime(2031, 3, 1, tzinfo=UTC))
    db.flush()
    assert D.open_run_km(db, TODAY, NEXT) == {CUR: pytest.approx(17.3)}
    assert D.open_run_km(db, TODAY, CUR) == {}  # planning the current week: nothing unfinished
    assert D.open_run_km(db, date(2031, 3, 24), NEXT) == {}
