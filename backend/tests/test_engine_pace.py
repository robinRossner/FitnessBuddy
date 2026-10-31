"""VDOT maths, the PaceModel protocol and HR zones."""

import pytest

from apps.fitness.engine.config import EngineConfig, HeartRateConfig, load_engine_config
from apps.fitness.engine.hr import compute_zones, zones_from_config
from apps.fitness.engine.pace import (
    FRAC_INTERVAL,
    FRAC_THRESHOLD,
    PaceModel,
    PaceObservation,
    VdotPaceModel,
    fmt_pace,
    make_pace_model,
    pace_at_fraction,
    race_time_s,
    vdot_for_pace,
    vdot_from_race,
)
from apps.fitness.settings import FITNESS_DIR

V3 = load_engine_config(FITNESS_DIR / "config" / "engine.v3.yaml")


def test_vdot_known_table_values():
    # Daniels: VDOT 40 -> 5 km 24:08, 10 km 50:03 (table is rounded; allow a few seconds)
    assert race_time_s(40, 5000) == pytest.approx(24 * 60 + 8, abs=4)
    assert race_time_s(40, 10000) == pytest.approx(50 * 60 + 3, abs=4)
    assert vdot_from_race(5000, 24 * 60 + 8) == pytest.approx(40, abs=0.1)
    # threshold / interval pace at VDOT 50: 4:15/km and 3:54/km
    assert fmt_pace(pace_at_fraction(50, FRAC_THRESHOLD)) == "4:15"
    assert fmt_pace(pace_at_fraction(50, FRAC_INTERVAL)) == "3:54"


def test_seed_is_10k_in_52_30():
    m = VdotPaceModel(10000, 52 * 60 + 30)
    assert m.vdot == pytest.approx(37.79, abs=0.05)
    assert m.describe()["source"] == "seed"


def test_pace_vdot_roundtrip_and_faster_pace():
    p = pace_at_fraction(45, FRAC_INTERVAL)
    assert vdot_for_pace(p, FRAC_INTERVAL) == pytest.approx(45, abs=1e-6)
    m = VdotPaceModel(step=0.5)
    assert m.faster_pace(250, 1) < 250
    assert m.faster_pace(250, 2) < m.faster_pace(250, 1)
    assert 1 < 250 - m.faster_pace(250, 1) < 5  # a small step, not a jump


def test_fit_raises_vdot_only_with_hard_enough_long_enough_efforts():
    m = VdotPaceModel(10000, 49 * 60, min_effort_m=5000, min_effort_hr=154)
    easy_long = PaceObservation(8000, 8000 * 0.32, avg_hr=150)  # fast but easy HR
    short_hard = PaceObservation(3000, 3000 * 0.25, avg_hr=170)  # too short
    no_hr = PaceObservation(10000, 2500, avg_hr=None)
    m.fit([easy_long, short_hard, no_hr])
    assert m.describe()["source"] == "seed"
    hard = PaceObservation(10000, 10000 * 0.29, avg_hr=158)  # 10 km in 48:20
    slower = PaceObservation(5000, 5000 * 0.40, avg_hr=170)
    m.fit([slower, hard])
    assert m.vdot > m.seed_vdot
    assert m.describe()["source"] == "effort" and m.describe()["best_effort"]["distance_m"] == 10000
    m.fit([])  # refit resets
    assert m.vdot == m.seed_vdot


def test_interval_and_easy_paces_are_ordered():
    m = VdotPaceModel(10000, 52 * 60 + 30)
    easy, rep = m.easy_pace(), m.interval_pace(1000)
    assert easy.min_s_per_km < easy.max_s_per_km
    assert rep.max_s_per_km < easy.min_s_per_km  # intervals clearly faster than easy
    assert m.interval_pace(400).min_s_per_km < rep.min_s_per_km  # short reps are faster


def test_pace_model_selected_by_config():
    model = make_pace_model(V3)
    assert isinstance(model, PaceModel) and model.name == "vdot"
    bad = EngineConfig.model_validate(
        {**V3.model_dump(mode="json"), "running": {"pace_model": "nope"}}
    )
    with pytest.raises(ValueError, match="unknown pace_model"):
        make_pace_model(bad)


def test_zones_from_lthr_171_friel():
    z = zones_from_config(V3)
    assert (z.max_hr, z.lthr) == (190, 171)
    assert [(x.name, x.low, x.high) for x in z.zones] == [
        ("z1", 0, 149),
        ("z2", 150, 156),
        ("z3", 157, 165),
        ("z4", 166, 173),
        ("z5a", 174, 179),
        ("z5b", 180, 186),
        ("z5c", 187, 190),
    ]
    assert z.easy_high == 156 and z.easy_basis == "lthr"  # top of Z2 = 89 % LTHR
    assert z.easy_low == 124  # 65 % of max HR 190
    assert (z.interval_low, z.interval_high) == (166, 179)  # Z4 .. Z5a
    assert z.zone_of(150) == "z2" and z.zone_of(170) == "z4" and z.zone_of(190) == "z5c"


def test_zones_follow_lthr_and_fall_back_to_max_hr():
    hr = V3.running_cfg.heart_rate
    z178 = compute_zones(HeartRateConfig(**{**hr.model_dump(), "lthr": 178}))
    assert z178.easy_high == 158  # 0.89 * 178 = 158.4
    no_lthr = compute_zones(HeartRateConfig(**{**hr.model_dump(), "lthr": None}))
    assert no_lthr.easy_basis == "hrmax" and no_lthr.easy_high == 152  # floor(0.8 * 190)
    v2 = load_engine_config(FITNESS_DIR / "config" / "engine.v2.yaml")
    assert zones_from_config(v2).easy_high == 153  # 0.8 * 192
