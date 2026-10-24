from datetime import date

import pytest

from app.stats import (
    E1RMContext,
    SetLike,
    aggregate_session,
    epley,
    mark_prs,
    set_e1rm,
)


def test_epley():
    assert epley(100, 10) == pytest.approx(133.33, abs=0.01)
    assert epley(80, 5) == pytest.approx(93.33, abs=0.01)
    assert epley(100, 1) == 100
    assert epley(100, 15) == pytest.approx(150.0)
    assert epley(100, 16) is None  # above the cap
    assert epley(100, 20, max_reps=20) == pytest.approx(166.67, abs=0.01)
    assert epley(0, 10) is None
    assert epley(100, 0) is None


BARBELL = E1RMContext("barbell", "total", True)
BW = E1RMContext("bodyweight", "total", True)


def test_set_e1rm_rules():
    assert set_e1rm(100, 5, "working", BARBELL) == (pytest.approx(116.67, abs=0.01), "total")
    assert set_e1rm(100, 5, "failure", BARBELL) is not None
    assert set_e1rm(100, 5, "warmup", BARBELL) is None
    assert set_e1rm(100, 5, "drop", BARBELL) is None
    assert set_e1rm(100, None, "working", BARBELL) is None  # timed hold
    assert set_e1rm(100, 5, "working", E1RMContext("barbell", "total", False)) is None


def test_set_e1rm_bodyweight_and_assistance():
    assert set_e1rm(0, 10, "working", BW) is None  # bodyweight unknown
    assert set_e1rm(10, 5, "working", BW) == (pytest.approx(11.67, abs=0.01), "added_weight")
    known = E1RMContext("bodyweight", "total", True, bodyweight_kg=80)
    assert set_e1rm(10, 5, "working", known) == (pytest.approx(105.0), "total")
    assisted = E1RMContext("machine_stack", "assistance", True)
    assert set_e1rm(20, 8, "working", assisted) is None
    assisted_bw = E1RMContext("machine_stack", "assistance", True, bodyweight_kg=80)
    assert set_e1rm(20, 8, "working", assisted_bw)[0] == pytest.approx(76.0)


def _agg(day, sets):
    likes = []
    for w, r, t in sets:
        res = set_e1rm(w, r, t, BARBELL)
        likes.append(SetLike(w, r, t, e1rm=res[0] if res else None))
    return aggregate_session("s", 1, day, likes)


def test_aggregate_and_prs():
    a = _agg(date(2024, 1, 1), [(60, 10, "warmup"), (100, 5, "working"), (100, 5, "working")])
    b = _agg(date(2024, 1, 8), [(100, 6, "working"), (90, 8, "working")])
    c = _agg(date(2024, 1, 15), [(105, 3, "working")])
    assert a.set_count == 3 and a.working_set_count == 2 and a.total_volume_kg == 1000
    assert (a.top_set_weight_kg, a.top_set_reps) == (100, 5)
    mark_prs([a, b, c])
    assert not a.is_e1rm_pr and not a.is_weight_pr  # baseline
    assert b.is_e1rm_pr and not b.is_weight_pr
    assert b.rep_prs == [{"weight_kg": 100, "reps": 6, "previous": 5}]
    assert c.is_weight_pr and not c.is_e1rm_pr  # 105x3 = 115.5 < 100x6 = 120


def test_best_hold():
    agg = aggregate_session(
        "s",
        1,
        date(2024, 1, 1),
        [SetLike(0, None, "working", duration_s=30), SetLike(0, None, "working", duration_s=45)],
    )
    assert agg.best_hold_s == 45 and agg.best_e1rm_kg is None


def test_bodyweight_loaded_uses_bodyweight_plus_added():
    ctx = E1RMContext("bodyweight", "total", True, bodyweight_kg=82)
    assert set_e1rm(0, 10, "working", ctx) == (pytest.approx(100.0), "total")
    assert set_e1rm(20, 5, "working", ctx) == (pytest.approx(110.83, abs=0.01), "total")
    # not bodyweight-loaded (weighted crunch): only the added plate counts
    plate = E1RMContext("bodyweight", "total", True, bodyweight_kg=82, bodyweight_loaded=False)
    assert set_e1rm(10, 10, "working", plate) == (pytest.approx(13.33, abs=0.01), "added_weight")
    assert set_e1rm(0, 10, "working", plate) is None


def test_assistance_is_bodyweight_minus_assistance():
    machine = E1RMContext("machine_stack", "assistance", True, bodyweight_kg=82)
    assert set_e1rm(20, 8, "working", machine) == (
        pytest.approx(55 * (1 + 8 / 30), abs=0.01),
        "total",
    )
    assert set_e1rm(0, 8, "working", machine) is not None  # no help = full bodyweight
    assert set_e1rm(75, 8, "working", machine) is None  # more help than bodyweight
    band = E1RMContext("bodyweight", "assistance", True, bodyweight_kg=82)
    assert set_e1rm(5, 10, "working", band) == (
        pytest.approx(70 * (1 + 10 / 30), abs=0.01),
        "total",
    )
    assert set_e1rm(0, 10, "working", band) is None  # band assistance unknown: skipped
    assert set_e1rm(5, 10, "working", E1RMContext("bodyweight", "assistance", True)) is None
