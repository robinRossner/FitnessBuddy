"""Planned sessions per week: a missed lifting session and its make-up count once."""

from datetime import date

from apps.fitness.analytics.planned import planned_counts
from apps.fitness.db.models import PlannedSession

WEEK = date(2026, 11, 16)


def _row(day, kind, status="skipped", moved_from=None, deleted=False):
    from apps.fitness.db.types import utcnow

    return PlannedSession(
        day=day,
        kind=kind,
        status=status,
        moved_from=moved_from,
        deleted_at=utcnow() if deleted else None,
    )


def _week(db, rows):
    db.add_all(rows)
    db.commit()
    return planned_counts(db, [WEEK])[WEEK]


def test_missed_and_made_up_counts_once(db):
    d = lambda n: date(2026, 11, n)  # noqa: E731
    rows = [
        _row(d(16), "upper"),
        _row(d(17), "lower"),
        _row(d(18), "upper", moved_from=d(16)),
        _row(d(19), "upper"),
        _row(d(20), "rest", status="done"),
        _row(d(21), "upper", moved_from=d(19)),
        _row(d(22), "rest", status="done"),
    ]
    assert _week(db, rows) == (3, 0)


def test_skipped_without_makeup_still_counts(db):
    d = lambda n: date(2026, 11, n)  # noqa: E731
    rows = [
        _row(d(16), "upper"),
        _row(d(17), "lower", status="done"),
        _row(d(18), "rest", status="done"),
        _row(d(19), "upper", status="done"),
        _row(d(20), "rest", status="done"),
        _row(d(21), "rest", status="done"),
        _row(d(22), "rest", status="done"),
    ]
    assert _week(db, rows) == (3, 0)


def test_deleted_makeup_does_not_hide_original(db):
    d = lambda n: date(2026, 11, n)  # noqa: E731
    rows = [
        _row(d(16), "upper"),
        _row(d(17), "lower", status="done"),
        _row(d(18), "rest", status="done"),
        _row(d(19), "upper", status="done"),
        _row(d(20), "upper", moved_from=d(16), deleted=True),
        _row(d(21), "rest", status="done"),
        _row(d(22), "rest", status="done"),
    ]
    assert _week(db, rows) == (3, 0)
