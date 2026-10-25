"""Config versioning in the DB: seed, create_version, rollback, Garmin auto-update."""

from datetime import date

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select

from app.db.models import AthleteMetric, ConfigVersion, DecisionLog
from app.engine import config_service as cs
from app.engine.config import EngineConfig
from app.engine.hr import zones_from_config
from app.settings import BACKEND_DIR

V3_PATH = BACKEND_DIR / "config" / "engine.v3.yaml"


@pytest.fixture
def seeded(db):
    cs.ensure_seeded(db, V3_PATH)
    db.commit()
    return db


def add_metric(db, metric: str, value: float, day: date):
    db.add(AthleteMetric(day=day, metric=metric, value=value, source="garmin"))
    db.flush()


def test_seed_is_active_and_idempotent(seeded):
    active = cs.get_active(seeded)
    assert active.version == "v3" and active.author == "seed" and active.parent_id is None
    cs.ensure_seeded(seeded, V3_PATH)
    assert seeded.scalar(select(ConfigVersion.id).where(ConfigVersion.version == "v3")) == active.id
    cfg, _ = cs.load_active(seeded)
    assert cfg.running_cfg.heart_rate.lthr == 171 and cfg.running_cfg.heart_rate.max_hr == 190
    assert cfg.running_cfg.heart_rate.source == "garmin"


def test_create_version_records_parent_diff_reason_author(seeded):
    v3 = cs.get_active(seeded)
    new = cs.create_version(
        seeded,
        {"strength.rep_range": [6, 10], "increments.dumbbell_increment_kg": 2.5},
        reason="user prefers heavier",
        author="user",
    )
    assert new.version == "v3.1" and new.is_active and not v3.is_active
    assert new.parent_id == v3.id and new.author == "user" and new.reason == "user prefers heavier"
    assert new.diff["strength.rep_range"] == {"old": [7, 12], "new": [6, 10]}
    assert new.diff["increments.dumbbell_increment_kg"]["new"] == 2.5
    assert new.content["version"] == "v3.1"
    cfg, _ = cs.load_active(seeded)
    assert cfg.strength.rep_range == (6, 10)
    # the parent is untouched (immutable)
    assert seeded.get(ConfigVersion, v3.id).content["strength"]["rep_range"] == [7, 12]
    second = cs.create_version(
        seeded, {"strength.stall_exposures": 4}, reason="more patience", author="advisor"
    )
    assert second.version == "v3.2" and second.parent_id == new.id


def test_no_change_creates_no_version(seeded):
    before = len(cs.history(seeded))
    same = cs.create_version(seeded, {"strength.stall_exposures": 3}, reason="noop", author="user")
    assert same.version == "v3" and len(cs.history(seeded)) == before


def test_invalid_change_is_rejected_and_unknown_author(seeded):
    with pytest.raises(ValidationError):
        cs.create_version(
            seeded, {"increments.dumbbell_increment_kg": 3.0}, reason="x", author="user"
        )
    with pytest.raises(cs.ConfigError):
        cs.create_version(seeded, {"strength.stall_exposures": 5}, reason="x", author="robot")


def test_rollback_restores_content_as_a_new_version(seeded):
    cs.create_version(seeded, {"strength.stall_exposures": 5}, reason="a", author="user")
    cs.create_version(seeded, {"strength.stall_exposures": 6}, reason="b", author="user")
    row = cs.rollback(seeded, "v3")
    assert row.version == "v3.3" and row.is_active
    assert (
        row.reason == "rollback to v3" and row.author == "user" and row.meta["rollback_of"] == "v3"
    )
    cfg, active = cs.load_active(seeded)
    assert cfg.strength.stall_exposures == 3 and active.id == row.id
    assert [v.version for v in cs.history(seeded)][:2] == ["v3.3", "v3.2"]  # history kept
    with pytest.raises(cs.ConfigError):
        cs.rollback(seeded, "v99")
    assert cs.rollback(seeded, "v3.3").id == row.id  # already active: nothing new


def test_garmin_same_values_do_nothing(seeded):
    add_metric(seeded, "max_hr", 190, date(2026, 10, 25))
    add_metric(seeded, "lthr", 171, date(2026, 10, 25))
    assert cs.apply_garmin_hr(seeded) is None
    assert cs.get_active(seeded).version == "v3"


def test_garmin_new_lthr_creates_engine_version_and_recomputes_zones(seeded):
    add_metric(seeded, "lthr", 171, date(2026, 10, 25))
    add_metric(seeded, "lthr", 178, date(2026, 11, 8))
    new = cs.apply_garmin_hr(seeded)
    assert new is not None and new.author == "engine" and new.version == "v3.1"
    assert new.reason == "Garmin reported LTHR 171→178 on 2026-11-08"
    assert new.diff == {"running.heart_rate.lthr": {"old": 171.0, "new": 178}}
    cfg, _ = cs.load_active(seeded)
    assert zones_from_config(cfg).easy_high == 158  # round(0.89 * 178)
    # decision logged with inputs + output
    d = seeded.scalar(select(DecisionLog).where(DecisionLog.kind == "config_update"))
    assert d.output["rule"] == "garmin_hr_autoupdate" and d.output["new_version"] == "v3.1"
    assert d.config_version_id == new.id and "LTHR" in d.rationale
    # a second sync with the same data does nothing
    assert cs.apply_garmin_hr(seeded) is None
    assert len(cs.history(seeded)) == 2


def test_garmin_max_hr_and_lthr_together(seeded):
    add_metric(seeded, "max_hr", 192, date(2026, 11, 1))
    add_metric(seeded, "lthr", 172, date(2026, 11, 1))
    new = cs.apply_garmin_hr(seeded)
    assert "LTHR 171→172" in new.reason and "max HR 190→192" in new.reason
    cfg, _ = cs.load_active(seeded)
    hr = cfg.running_cfg.heart_rate
    assert (hr.lthr, hr.max_hr) == (172, 192)


def test_user_rollback_sticks_until_garmin_reports_something_newer(seeded):
    add_metric(seeded, "lthr", 178, date(2026, 11, 8))
    auto = cs.apply_garmin_hr(seeded)
    assert auto.version == "v3.1"
    back = cs.rollback(seeded, "v3")  # user does not want 178
    assert back.version == "v3.2" and cs.load_active(seeded)[0].running_cfg.heart_rate.lthr == 171
    assert cs.apply_garmin_hr(seeded) is None  # same Garmin value is not re-applied
    add_metric(seeded, "lthr", 180, date(2026, 12, 1))
    newer = cs.apply_garmin_hr(seeded)
    assert newer is not None and newer.diff["running.heart_rate.lthr"]["new"] == 180


def test_manual_source_is_not_auto_updated(seeded):
    cs.create_version(seeded, {"running.heart_rate.source": "manual"}, reason="pin", author="user")
    add_metric(seeded, "lthr", 190, date(2026, 11, 8))
    assert cs.apply_garmin_hr(seeded) is None


def test_validated_config_roundtrip(seeded):
    row = cs.get_active(seeded)
    assert EngineConfig.model_validate(row.content).version == "v3"


def test_on_new_data_hook_applies_garmin_values_and_never_raises(seeded, monkeypatch):
    from contextlib import contextmanager
    from types import SimpleNamespace

    from app.jobs import garmin_jobs

    @contextmanager
    def scope():
        yield seeded
        seeded.commit()

    add_metric(seeded, "lthr", 179, date(2026, 11, 20))
    seeded.commit()
    monkeypatch.setattr(garmin_jobs, "session_scope", scope)
    monkeypatch.setattr(garmin_jobs, "get_settings", lambda: SimpleNamespace(engine_auto_run=False))
    garmin_jobs.on_new_data(SimpleNamespace(results=[]))
    active = cs.get_active(seeded)
    assert active.version == "v3.1" and active.author == "engine"
    assert "LTHR 171→179" in active.reason
    # a failing engine run must not break the sync
    monkeypatch.setattr(garmin_jobs, "get_settings", lambda: SimpleNamespace(engine_auto_run=True))
    monkeypatch.setattr("app.engine.service.run_engine", lambda *a, **k: 1 / 0)
    garmin_jobs.on_new_data(SimpleNamespace(results=[]))


def test_decision_log_dedupes_per_rule_on_a_shared_subject(seeded):
    from app.engine.decisions import log_decision

    kw = {"kind": "running", "subject_type": "week", "subject_id": "w1", "rationale": "x"}
    for _ in range(2):  # two rules on one subject, logged alternately: still one row each
        log_decision(seeded, rule="a", inputs={"i": 1}, output={"o": 1}, **kw)
        log_decision(seeded, rule="b", inputs={"i": 1}, output={"o": 1}, **kw)
    n = seeded.scalar(
        select(func.count()).select_from(DecisionLog).where(DecisionLog.subject_id == "w1")
    )
    assert n == 2
    assert (
        log_decision(seeded, rule="a", inputs={"i": 1}, output={"o": 2}, **kw) is not None
    )  # changed


def test_volume_floor_is_added_as_a_new_version_not_by_editing_v3(seeded):
    v3 = cs.get_active(seeded)
    assert "weekly_volume_floor_km" not in v3.content["running"]
    new = cs.create_version(
        seeded,
        {"running.weekly_volume_floor_km": 18},
        reason="10 % cap is meaningless on a ~9 km/week base: never cap below 18 km/week",
        author="user",
    )
    assert new.version == "v3.1" and new.parent_id == v3.id
    assert new.diff == {"running.weekly_volume_floor_km": {"old": None, "new": 18}}
    assert cs.load_active(seeded)[0].running_cfg.weekly_volume_floor_km == 18
    assert "weekly_volume_floor_km" not in seeded.get(ConfigVersion, v3.id).content["running"]
