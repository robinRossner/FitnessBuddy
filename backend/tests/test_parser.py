from datetime import UTC, datetime
from pathlib import Path

import pytest

from apps.fitness.db.types import SessionKind, SetType
from apps.fitness.importers.strong.parser import (
    ParseError,
    assign_blocks,
    derive_session_kind,
    parse_duration,
    parse_file,
    parse_number,
    parse_set_order,
    parse_start,
    to_utc,
)

FIX = Path(__file__).parent / "fixtures"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1h 7m", 4020),
        ("45m", 2700),
        ("1h", 3600),
        ("2h 0m", 7200),
        ("30s", 30),
        ("1:07:30", 4050),
        ("", None),
        (None, None),
    ],
)
def test_parse_duration(raw, expected):
    assert parse_duration(raw) == expected


def test_parse_duration_rejects_garbage():
    with pytest.raises(ParseError):
        parse_duration("about an hour")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("12.5", 12.5),
        ("12,5", 12.5),
        ("1.234,5", 1234.5),
        ("1,234.5", 1234.5),
        ("", None),
        ("0", 0.0),
    ],
)
def test_parse_number(raw, expected):
    assert parse_number(raw) == expected


def test_parse_start_formats():
    assert parse_start("2025-10-01 07:31:02") == datetime(2025, 10, 1, 7, 31, 2)
    assert parse_start("2025-10-01 07:31") == datetime(2025, 10, 1, 7, 31)


def test_set_order_markers():
    assert parse_set_order("3") == (SetType.WORKING, None)
    assert parse_set_order("F") == (SetType.FAILURE, None)
    assert parse_set_order("W") == (SetType.WARMUP, None)
    assert parse_set_order("d") == (SetType.DROP, None)
    st, problem = parse_set_order("X")
    assert st is SetType.WORKING and problem


def test_to_utc_regular_summer_and_winter():
    assert to_utc(datetime(2025, 7, 1, 12, 0), "Europe/Vienna") == (
        datetime(2025, 7, 1, 10, 0, tzinfo=UTC),
        None,
    )
    assert to_utc(datetime(2025, 1, 1, 12, 0), "Europe/Vienna") == (
        datetime(2025, 1, 1, 11, 0, tzinfo=UTC),
        None,
    )


def test_to_utc_dst_ambiguous_uses_fold0():
    # 2024-10-27 02:30 happens twice in Vienna; fold=0 = first occurrence (CEST, UTC+2)
    utc, flag = to_utc(datetime(2024, 10, 27, 2, 30), "Europe/Vienna")
    assert flag == "ambiguous"
    assert utc == datetime(2024, 10, 27, 0, 30, tzinfo=UTC)


def test_to_utc_dst_nonexistent_flagged():
    # 2024-03-31 02:30 does not exist in Vienna (clocks jump 02:00 -> 03:00)
    utc, flag = to_utc(datetime(2024, 3, 31, 2, 30), "Europe/Vienna")
    assert flag == "nonexistent"
    assert utc == datetime(2024, 3, 31, 1, 30, tzinfo=UTC)


def test_session_kind():
    assert derive_session_kind("Push (PPL)") is SessionKind.PUSH
    assert derive_session_kind("Legs (PPL)") is SessionKind.LEGS
    assert derive_session_kind("Upper B") is SessionKind.UPPER
    assert derive_session_kind("Lower") is SessionKind.LOWER
    assert derive_session_kind("Callisthenics") is SessionKind.CALISTHENICS
    assert derive_session_kind("Morning Workout") is SessionKind.OTHER


def test_parse_basic_file():
    pf = parse_file(FIX / "strong_basic.csv")
    assert pf.is_strong and pf.delimiter == ","
    assert pf.rows_read == 19
    assert len(pf.workouts) == 4  # two workouts on 2024-11-02 stay separate
    upper = pf.workouts[datetime(2024, 3, 31, 2, 30)]
    assert upper.duration_s == 4020 and upper.name == "Upper"
    bench = [r for r in upper.rows if r.exercise_name == "Bench Press (Dumbbell)"]
    assert [r.set_type for r in bench] == [SetType.WORKING, SetType.WORKING, SetType.FAILURE]
    assert [r.set_index for r in bench] == [1, 2, 3]  # ordinal from position, not 'F'
    assert bench[0].reps == 10 and isinstance(bench[0].reps, int)


def test_timed_hold_and_zero_weight():
    pf = parse_file(FIX / "strong_basic.csv")
    plank = next(r for wk in pf.workouts.values() for r in wk.rows if r.exercise_name == "Plank")
    assert plank.reps is None and plank.duration_hold_s == 60.0 and plank.is_timed_hold
    pull = next(r for wk in pf.workouts.values() for r in wk.rows if r.exercise_name == "Pull Up")
    assert pull.weight_kg == 0.0  # bodyweight stays 0, not None


def test_cardio_row():
    pf = parse_file(FIX / "strong_basic.csv")
    run = next(r for wk in pf.workouts.values() for r in wk.rows if r.exercise_name == "Running")
    assert run.is_cardio and run.distance_m == 5000.0 and run.duration_hold_s == 1800.0


def test_duplicate_exercise_blocks_in_one_workout():
    pf = parse_file(FIX / "strong_basic.csv")
    upper = pf.workouts[datetime(2024, 3, 31, 2, 30)]
    pulls = [
        (r.block_occurrence, r.set_index, r.exercise_order)
        for r in upper.rows
        if r.exercise_name == "Pull Up"
    ]
    assert pulls == [(1, 1, 2), (1, 2, 2), (2, 1, 4), (2, 2, 4)]


def test_consecutive_blocks_split_on_set_order_reset():
    pf = parse_file(FIX / "strong_basic.csv")
    rows = pf.workouts[datetime(2024, 3, 31, 2, 30)].rows
    pulls = [r for r in rows if r.exercise_name == "Pull Up"]
    assign_blocks(pulls)  # now consecutive with Set Order 1,2,1,2
    assert [(r.block_occurrence, r.set_index) for r in pulls] == [(1, 1), (1, 2), (2, 1), (2, 2)]


def test_semicolon_comma_decimal_lb_and_notes():
    pf = parse_file(FIX / "strong_semicolon_lb.csv")
    assert pf.delimiter == ";"
    wk = pf.workouts[datetime(2024, 5, 1, 10, 0)]
    first = wk.rows[0]
    assert first.weight_kg == pytest.approx(135.5 * 0.45359237, abs=1e-3)
    assert first.reps == 8 and first.rpe == 8.5
    assert first.notes == "felt good" and wk.notes == "quick one"
    run = wk.rows[2]
    assert run.distance_m == pytest.approx(3218.7, abs=0.1)  # 2 mi


def test_lb_unit_in_header(tmp_path):
    p = tmp_path / "x.csv"
    p.write_text(
        "Date,Workout Name,Duration,Exercise Name,Set Order,Weight (lbs),Reps\n"
        "2024-01-01 10:00:00,A,1h,Squat (Barbell),1,100,5\n",
        encoding="utf-8",
    )
    row = parse_file(p).workouts[datetime(2024, 1, 1, 10)].rows[0]
    assert row.weight_kg == pytest.approx(45.359, abs=1e-3)


def test_bom_and_non_strong_file(tmp_path):
    p = tmp_path / "bom.csv"
    p.write_text(
        "﻿Date,Workout Name,Duration,Exercise Name,Set Order,Weight,Reps\n"
        "2024-01-01 10:00:00,A,1h,Squat (Barbell),1,100,5\n",
        encoding="utf-8",
    )
    assert parse_file(p).rows_read == 1
    other = tmp_path / "garmin.csv"
    other.write_text("Activity Type,Date,Distance\nRunning,2024-01-01,5\n", encoding="utf-8")
    assert parse_file(other).is_strong is False
