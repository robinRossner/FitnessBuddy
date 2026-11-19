"""Build a synthetic demo database: a fictional lifter / runner, deterministic (seeded RNG).

    cd backend && uv run python -m apps.fitness.scripts.seed_demo_db [--out var/demo/fitness.db]

Everything the example generators (gen_*_examples.py) and the README show comes from this
database, so no real person's data is anywhere in the repository. The data goes through the real
code paths where that is cheap: a generated Strong CSV is read by the real importer (gyms,
variants, e1RM, session stats), the engine is run week by week for the last weeks so the plan,
VDOT and decision history are what the engine really produces. Garmin-style rows (runs, laps,
daily health, athlete metrics) and bodyweight entries are written directly.

The fictional lifter: about 82 kg, trains Upper Mon / Thu and Lower Tue, runs easy on Wednesday,
intervals on Friday and a long run on Sunday. "Today" in the examples is Sunday 2026-10-25.
"""

from __future__ import annotations

import argparse
import csv
import os
import random
import shutil
import tempfile
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from alembic import command
from alembic.config import Config
from sqlalchemy.orm import sessionmaker

from apps.fitness.db.models import AthleteMetric, BodyMeasurement, DailyHealth, Lap, Run
from apps.fitness.db.session import make_engine
from apps.fitness.db.types import MeasurementSource, Source
from apps.fitness.engine.store import config_service as cs
from apps.fitness.importers.strong.importer import ImportOptions, run_import
from apps.fitness.settings import BACKEND_DIR, FITNESS_DIR, get_settings
from apps.fitness.stats import rebuild_stats

TODAY = date(2026, 10, 25)  # a Sunday
FIRST_MONDAY = date(2026, 4, 27)  # 26 weeks of history
TZ = "Europe/Vienna"
BODYWEIGHT_KG = 82.0
SEED = 20261025

# (Strong name, kg at the start, kg gained over the 26 weeks, step, rep range)
UPPER = [
    ("Bench Press (Barbell)", 67.5, 10.0, 2.5, (7, 11)),
    ("Pull Up", 0.0, 7.5, 2.5, (7, 10)),
    ("Seated Overhead Press (Dumbbell)", 18.0, 4.0, 2.0, (8, 12)),
    ("Seated Row (Cable)", 50.0, 10.0, 2.5, (9, 12)),
    ("Lateral Raise (Cable)", 7.5, 2.5, 1.25, (10, 14)),
    ("Triceps Extension (Cable)", 22.5, 5.0, 2.5, (8, 12)),
    ("Bicep Curl (Dumbbell)", 12.0, 4.0, 2.0, (8, 12)),
]
LOWER = [
    ("Squat (Smith Machine)", 80.0, 15.0, 5.0, (6, 10)),
    ("Romanian Deadlift (Barbell)", 85.0, 15.0, 5.0, (7, 11)),
    ("Lying Leg Curl (Machine)", 40.0, 10.0, 2.5, (9, 13)),
    ("Leg Extension (Machine)", 50.0, 10.0, 2.5, (10, 14)),
    ("Standing Calf Raise (Machine)", 70.0, 10.0, 5.0, (10, 15)),
    ("Cable Crunch", 35.0, 10.0, 2.5, (10, 15)),
]
TWO_SETS = {"Bicep Curl (Dumbbell)", "Cable Crunch", "Leg Extension (Machine)"}
# the second gym (from 2026-08-17, see data/catalog/gyms.yaml) has other stacks / pulleys
GYM2_FACTOR = {
    "Seated Row (Cable)": 0.9,
    "Lateral Raise (Cable)": 0.8,
    "Lying Leg Curl (Machine)": 1.1,
}
GYM2_START = date(2026, 8, 17)
STALLED = {"Triceps Extension (Cable)": 6}  # no progress for the last 6 weeks (a coach example)


def _round_to(x: float, step: float) -> float:
    return round(round(x / step) * step, 2)


def lifting_csv(path: Path, rng: random.Random) -> None:
    header = ["Date", "Workout Name", "Duration", "Exercise Name", "Set Order", "Weight", "Reps"]
    rows = [[*header, "Distance", "Seconds", "RPE"]]
    weeks = (TODAY - FIRST_MONDAY).days // 7 + 1  # 26
    for w in range(weeks):
        monday = FIRST_MONDAY + timedelta(weeks=w)
        for offset, kind, table in ((0, "Upper", UPPER), (1, "Lower", LOWER), (3, "Upper", UPPER)):
            day = monday + timedelta(days=offset)
            if day >= TODAY or rng.random() < 0.07:  # a missed session now and then
                continue
            start = datetime.combine(day, time(18, rng.choice([0, 5, 10, 15, 30])))
            stamp = start.strftime("%Y-%m-%d %H:%M:%S")
            dur = f"{rng.randint(52, 74)}m"
            for name, base, gain, step, (lo, hi) in table:
                frac = w / (weeks - 1)
                held = STALLED.get(name)
                if held and w > weeks - 1 - held:
                    frac = (weeks - 1 - held) / (weeks - 1)  # flat at the plateau
                kg = base + gain * frac
                if day >= GYM2_START:
                    kg *= GYM2_FACTOR.get(name, 1.0)
                kg = max(0.0, _round_to(kg, step))
                n_sets = 2 if name in TWO_SETS else 3
                reps = rng.randint(lo, hi)
                if name in (UPPER[0][0], LOWER[0][0]):
                    rows.append(
                        [stamp, kind, dur, name, "W", _round_to(kg * 0.6, 2.5), 8, 0, 0, ""]
                    )
                for i in range(1, n_sets + 1):
                    r = max(lo - 1, min(hi + 1, reps - (i - 1) + rng.choice([0, 0, 1])))
                    rows.append([stamp, kind, dur, name, i, kg, r, 0, 0, ""])
    with path.open("w", newline="", encoding="utf-8") as f:
        csv.writer(f).writerows(rows)


def _utc(local: datetime) -> datetime:
    return local.replace(tzinfo=ZoneInfo(TZ)).astimezone(UTC)


def add_run(db, rng, day: date, title: str, kind: str, laps: list[tuple], *, hour: int) -> None:
    """laps: (distance_m, duration_s, avg_hr, intensity)."""
    start_local = datetime.combine(day, time(hour, rng.randint(0, 40)))
    dist = sum(lp[0] for lp in laps)
    dur = sum(lp[1] for lp in laps)
    hr = sum(lp[2] * lp[1] for lp in laps) / dur
    zones = {"z1": 0, "z2": 0, "z3": 0, "z4": 0, "z5": 0}
    for _d, t, h, _i in laps:
        z = "z1" if h < 150 else "z2" if h < 157 else "z3" if h < 166 else "z4" if h < 174 else "z5"
        zones[z] += int(t)
    run = Run(
        source=Source.GARMIN,
        external_id=str(21_000_000_000 + (day - date(2026, 1, 1)).days * 10 + hour),
        started_at_utc=_utc(start_local),
        started_at_local=start_local,
        tz=TZ,
        run_type=kind,
        activity_type="running",
        title=title,
        distance_m=dist,
        duration_s=dur,
        moving_time_s=dur - rng.randint(5, 40),
        avg_pace_s_per_km=dur / dist * 1000,
        avg_hr=round(hr, 1),
        max_hr=round(max(lp[2] for lp in laps) + rng.randint(5, 10), 0),
        elevation_gain_m=rng.randint(20, 90),
        avg_cadence=rng.choice([168, 170, 172, 174]),
        training_load=round(dur / 60 * (hr / 150) ** 2, 1),
        time_in_zones_s=zones,
        training_effect_aerobic=round(2.4 + (hr - 140) / 30, 1),
        training_effect_anaerobic=0.0 if kind != "intervals" else 1.4,
        vo2max=round(46.0 + (day - FIRST_MONDAY).days / 180 * 1.5, 0),
        calories=round(dist / 1000 * 62),
        detail_synced_at=_utc(start_local) + timedelta(hours=2),
        fidelity="high",
    )
    db.add(run)
    db.flush()
    t0 = 0.0
    for i, (d, t, h, inten) in enumerate(laps, start=1):
        db.add(
            Lap(
                run_id=run.id,
                lap_index=i,
                start_offset_s=t0,
                duration_s=t,
                distance_m=d,
                avg_hr=round(h, 1),
                max_hr=round(h + rng.randint(3, 7), 0),
                avg_pace_s_per_km=t / d * 1000,
                avg_cadence=run.avg_cadence,
                elevation_gain_m=rng.randint(0, 8),
                intensity=inten,
            )
        )
        t0 += t
    db.flush()


def runs(db, rng: random.Random) -> None:
    weeks = (TODAY - FIRST_MONDAY).days // 7 + 1
    for w in range(weeks):
        monday = FIRST_MONDAY + timedelta(weeks=w)
        gain = w / (weeks - 1)  # the fictional runner gets a bit fitter
        easy_pace = 362 - 14 * gain
        wed = monday + timedelta(days=2)  # easy run, 6 - 8 km in 1 km laps
        if wed < TODAY and rng.random() > 0.08:
            km = rng.choice([6, 6, 7, 8])
            laps = [
                (1000, easy_pace + rng.uniform(-6, 6), 143 + rng.uniform(-3, 5), None)
                for _ in range(km)
            ]
            add_run(db, rng, wed, "Easy run", "easy", laps, hour=7)
        fri = monday + timedelta(days=4)  # intervals, 4-6 x 1000 m with 400 m jog
        if fri < TODAY and rng.random() > 0.1:
            reps = 4 + min(2, w // 9)
            pace = 276 - 18 * gain
            laps = [(1000, 340, 140, "warmup")]
            for _ in range(reps):
                laps.append((1000, pace + rng.uniform(-3, 4), 169 + rng.uniform(-2, 4), "active"))
                laps.append((400, 175 + rng.uniform(-8, 8), 150, "recovery"))
            laps.append((1000, 345, 148, "cooldown"))
            add_run(db, rng, fri, f"Intervals {reps}x1000", "intervals", laps, hour=7)
        sun = monday + timedelta(days=6)  # long run
        if sun < TODAY and rng.random() > 0.06:
            km = min(16, 9 + w // 3 - (1 if w % 4 == 3 else 0) * 2)
            pace = easy_pace + 10
            laps = [
                (
                    1000,
                    pace + rng.uniform(-6, 6) + i * 0.8,
                    141 + i * 0.6 + rng.uniform(-2, 2),
                    None,
                )
                for i in range(km)
            ]
            add_run(db, rng, sun, "Long run", "long", laps, hour=9)


def health(db, rng: random.Random) -> None:
    for i in range(-1, 130):  # -1: Monday morning, the day the example plan is for
        day = TODAY - timedelta(days=i)
        slump = i < 4  # a few tired days up to today, so readiness is yellow-ish
        hrv = 66 + 8 * rng.uniform(-1, 1) - (9 if slump else 0)
        rhr = round(52 + rng.uniform(-2, 2) + (3 if slump else 0))
        sleep_h = max(5.4, min(8.4, rng.gauss(7.3, 0.6) - (0.6 if slump else 0)))
        deep, rem = 0.17 * sleep_h, 0.22 * sleep_h
        weekday = day.weekday() < 5
        db.add(
            DailyHealth(
                day=day,
                source=Source.GARMIN,
                resting_hr=rhr,
                hrv_overnight_ms=round(hrv, 1),
                hrv_status="UNBALANCED" if slump else "BALANCED",
                hrv_weekly_avg_ms=66 - (3 if i < 7 else 0),
                hrv_baseline_low_ms=58.0,
                hrv_baseline_high_ms=74.0,
                sleep_s=int(sleep_h * 3600),
                sleep_score=round(max(55, min(94, 60 + (sleep_h - 6) * 12 + rng.uniform(-4, 4)))),
                sleep_deep_s=int(deep * 3600),
                sleep_rem_s=int(rem * 3600),
                sleep_light_s=int((sleep_h - deep - rem - 0.3) * 3600),
                sleep_awake_s=int(0.3 * 3600),
                body_battery_charged=round(rng.uniform(60, 85)),
                body_battery_drained=round(rng.uniform(55, 80)),
                body_battery_max=round(rng.uniform(78, 96) - (15 if slump else 0)),
                body_battery_min=round(rng.uniform(12, 30)),
                stress_avg=round(rng.uniform(22, 38) + (6 if slump else 0)),
                steps=int(rng.gauss(9200 if weekday else 12500, 2200 if weekday else 3000)),
                training_readiness=round(rng.uniform(55, 85) - (20 if slump else 0)),
            )
        )
    for metric, value in (("max_hr", 190.0), ("lthr", 171.0), ("vo2max", 47.0)):
        db.add(AthleteMetric(day=date(2026, 6, 1), metric=metric, value=value, source="garmin"))
    # a weigh-in about weekly: 82 kg +- 0.6, drifting down 0.5 kg over the half year
    for i in range(0, 182, 6):
        day = TODAY - timedelta(days=i + 1)
        kg = round(BODYWEIGHT_KG + 0.5 * (i / 182) + rng.uniform(-0.6, 0.6), 1)
        at = _utc(datetime.combine(day, time(6, 50)))
        db.add(
            BodyMeasurement(
                measured_at=at,
                day=day,
                weight_kg=kg,
                source=MeasurementSource.MANUAL,
                updated_at=at,
            )
        )


def build(out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    out.unlink(missing_ok=True)
    for suffix in ("-wal", "-shm"):
        Path(str(out) + suffix).unlink(missing_ok=True)
    url = f"sqlite:///{out}"
    cfg = Config(str(FITNESS_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(FITNESS_DIR / "alembic"))
    cfg.attributes["url"] = url
    # like a real install: the first config (v3) goes in early, the later migrations then derive
    # their config versions (rest timers, progression v2, interval VDOT, ...) from it
    command.upgrade(cfg, "0004")
    factory = sessionmaker(bind=make_engine(url), expire_on_commit=False)
    with factory() as db:
        cs.ensure_seeded(db, FITNESS_DIR / "config" / "engine.v3.yaml")
        db.commit()
    command.upgrade(cfg, "head")

    os.environ["PROGRAM_LIVE_PATH"] = str(out.with_name("program.yaml"))
    get_settings.cache_clear()
    settings = get_settings()
    rng = random.Random(SEED)
    csv_path = Path(tempfile.mkdtemp()) / "demo_strong.csv"
    lifting_csv(csv_path, rng)
    shutil.copy(settings.program_path, out.with_name("program.yaml"))

    with factory() as db:
        res = run_import(
            db,
            ImportOptions(
                paths=[csv_path],
                mapping_path=settings.strong_mapping_path,
                catalog_path=settings.catalog_path,
                engine_config_path=settings.engine_config_path,
                tz=TZ,
                gym_name="Gym 1",
                gyms_path=settings.gyms_path,
                apply=True,
            ),
        )
        assert not res.error, res.error
        db.commit()
        health(db, rng)
        runs(db, rng)
        db.commit()
        rebuild_stats(db, bodyweight_kg=BODYWEIGHT_KG)
        db.commit()

    # the engine, week by week for the last weeks: plan, VDOT updates and decision log as the
    # engine really writes them (the stored plan of the example week is made by the generators)
    from apps.fitness.engine.store import service as engine_service

    for back in range(8, 0, -1):
        today = TODAY - timedelta(days=7 * back - 1)  # a Monday
        with (
            patch("apps.fitness.engine.store.service.user_today", lambda s=None, d=today: d),
            patch("apps.fitness.analytics.common.today", lambda d=today: d),
            factory() as db,
        ):
            engine_service.run_engine(db, today=today, weeks=1)
            db.commit()
    print("wrote", out)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(BACKEND_DIR / "var" / "demo" / "fitness.db"))
    args = ap.parse_args()
    build(Path(args.out))


if __name__ == "__main__":
    main()
