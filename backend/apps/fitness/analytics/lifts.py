"""Lift stats from the materialised `exercise_session_stats` (no set-level work except PR sets).

`GET /stats/lifts` (one summary per variant) and `GET /stats/lifts/{id}` (summary + points + PRs).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from apps.fitness.analytics import common
from apps.fitness.analytics.common import num
from apps.fitness.db.catalog import load_catalog, region_of_muscle
from apps.fitness.db.models import (
    AdvisorTrigger,
    DecisionLog,
    Exercise,
    ExerciseSessionStats,
    ExerciseVariant,
    Gym,
    WorkoutSet,
)
from apps.fitness.engine.core.ids import base_name_of
from apps.fitness.engine.core.lifting import detect_stall
from apps.fitness.engine.core.program import apply_overrides
from apps.fitness.engine.store import config_service as cs
from apps.fitness.engine.store import data as D
from apps.fitness.engine.store import service
from apps.fitness.settings import get_settings

TREND_DAYS = 28


@dataclass
class Row:
    session_id: str
    day: date
    working_sets: int
    volume_kg: float
    e1rm: float | None
    top_w: float | None
    top_r: int | None
    e1rm_pr: bool
    weight_pr: bool
    rep_prs: list


def _stall_resets(db: Session) -> dict[int, date]:
    """Day of the latest stall-ladder reset the engine decided per variant (decisions_log
    `output.ladder`, same source as `engine.store.data.load_ladder`), in one query."""
    out: dict[int, date] = {}
    q = (
        select(DecisionLog.subject_id, DecisionLog.output)
        .where(
            DecisionLog.kind == "progression",
            DecisionLog.subject_type == "variant",
            (func.json_extract(DecisionLog.output, "$.reset_day").is_not(None))
            | (func.json_extract(DecisionLog.output, "$.ladder").is_not(None)),
        )
        .order_by(DecisionLog.id.desc())
    )
    for sid, output in db.execute(q):
        if sid is None or not sid.isdigit() or int(sid) in out:
            continue
        o = output if isinstance(output, dict) else json.loads(output or "{}")
        lad = o.get("ladder") or {}
        if lad.get("day") and lad.get("step") in (1, 3):
            out[int(sid)] = date.fromisoformat(lad["day"])
        elif o.get("reset_day") and o.get("reset_load") is not None and "ladder" not in o:
            continue  # engine v1 -10 % resets are converted to a retest (data.legacy_windows)
    return out


def _open_stall_triggers(db: Session) -> set[int]:
    ids = db.scalars(
        select(AdvisorTrigger.subject_id).where(
            AdvisorTrigger.type == "lift_stall",
            AdvisorTrigger.status == "open",
            AdvisorTrigger.subject_type == "variant",
        )
    )
    return {int(i) for i in ids if i and i.isdigit()}


def _rows_by_variant(db: Session, variant_id: int | None = None) -> dict[int, list[Row]]:
    q = select(
        ExerciseSessionStats.variant_id,
        ExerciseSessionStats.session_id,
        ExerciseSessionStats.session_date,
        ExerciseSessionStats.working_set_count,
        ExerciseSessionStats.total_volume_kg,
        ExerciseSessionStats.best_e1rm_kg,
        ExerciseSessionStats.top_set_weight_kg,
        ExerciseSessionStats.top_set_reps,
        ExerciseSessionStats.is_e1rm_pr,
        ExerciseSessionStats.is_weight_pr,
        ExerciseSessionStats.rep_prs,
    ).order_by(ExerciseSessionStats.session_date, ExerciseSessionStats.id)
    if variant_id is not None:
        q = q.where(ExerciseSessionStats.variant_id == variant_id)
    out: dict[int, list[Row]] = {}
    for vid, *rest in db.execute(q):
        out.setdefault(vid, []).append(Row(*rest))
    return out


def _trend_4w_pct(rows: list[Row], today: date) -> float | None:
    """Best e1RM of the last 28 days vs the best of the 28 days before; None if either is empty."""
    recent_from = today - timedelta(days=TREND_DAYS - 1)
    prior_from = recent_from - timedelta(days=TREND_DAYS)
    recent = [r.e1rm for r in rows if r.e1rm and recent_from <= r.day <= today]
    prior = [r.e1rm for r in rows if r.e1rm and prior_from <= r.day < recent_from]
    if not recent or not prior:
        return None
    return (max(recent) - max(prior)) / max(prior) * 100


@dataclass
class Context:
    stall_n: int
    resets: dict[int, date]
    triggers: set[int]
    regions: dict[str, str]  # exercise slug -> coarse body region (catalog)
    program_ids: set[int]  # variants the program lists for the current gym


def _program_variant_ids(db: Session) -> set[int]:
    """Variants of the active program exercises, resolved as the engine does at the current gym."""
    s = get_settings()
    try:
        program = service.get_program(s)
    except (OSError, ValueError):  # no program file yet (FileNotFoundError) or an invalid one
        return set()
    program = apply_overrides(program, cs.load_active(db)[0].exercise_overrides)
    gym = D.current_gym(db, s.gyms_path)
    ids = set()
    for kind in program.days:
        for pe in program.exercises(kind):
            v = D.resolve_variant(db, pe.variant, gym)
            if v is not None:
                ids.add(v.id)
    return ids


def _summary(v, ex, gym_name, rows, today, ctx: Context) -> dict:
    stall_n, resets, triggers = ctx.stall_n, ctx.resets, ctx.triggers
    e1rms = [r.e1rm for r in rows if r.e1rm]
    reset_day = resets.get(v.id)
    window = [r for r in rows if reset_day is None or r.day >= reset_day]
    stalled = bool(v.active) and (
        detect_stall([r.e1rm for r in window], stall_n) or v.id in triggers
    )
    pr_days = [r.day for r in rows if r.e1rm_pr]
    return {
        "variant_id": v.id,
        "name": base_name_of(v),
        "exercise_id": ex.id,
        "muscle_group": ex.primary_muscle,
        "body_region": ctx.regions.get(ex.slug) or region_of_muscle(ex.primary_muscle),
        "gym_id": v.gym_id,
        "gym_name": gym_name,
        "active": bool(v.active),
        "in_program": v.id in ctx.program_ids,
        "sessions": len(rows),
        "first_date": rows[0].day.isoformat(),
        "last_date": rows[-1].day.isoformat(),
        "best_e1rm": num(max(e1rms)) if e1rms else None,
        "latest_e1rm": num(e1rms[-1]) if e1rms else None,
        "trend_4w_pct": num(_trend_4w_pct(rows, today)),
        "last_pr_date": max(pr_days).isoformat() if pr_days else None,
        "stalled": stalled,
    }


def _variant_meta(db: Session, ids: list[int] | None = None):
    q = (
        select(ExerciseVariant, Exercise, Gym.name)
        .join(Exercise, ExerciseVariant.exercise_id == Exercise.id)
        .join(Gym, ExerciseVariant.gym_id == Gym.id, isouter=True)
    )
    if ids is not None:
        q = q.where(ExerciseVariant.id.in_(ids))
    return {v.id: (v, ex, gym) for v, ex, gym in db.execute(q)}


def _context(db: Session) -> Context:
    cfg, _ = cs.load_active(db)
    catalog_path = get_settings().catalog_path
    regions = (
        {slug: c.body_region for slug, c in load_catalog(catalog_path).items()}
        if catalog_path.exists()
        else {}
    )
    return Context(
        cfg.strength.stall_exposures,
        _stall_resets(db),
        _open_stall_triggers(db),
        regions,
        _program_variant_ids(db),
    )


def lift_summaries(db: Session) -> list[dict]:
    cs.ensure_seeded(db, get_settings().engine_config_path)
    today = common.today()
    by_variant = _rows_by_variant(db)
    meta = _variant_meta(db, list(by_variant))
    ctx = _context(db)
    out = [
        _summary(*meta[vid], rows, today, ctx)
        for vid, rows in by_variant.items()
        if vid in meta and rows
    ]
    out.sort(key=lambda s: (s["last_date"], s["name"]), reverse=True)
    return out


def rows_by_variant(db: Session) -> dict[int, list[Row]]:
    """Per-session stats rows per variant, oldest first (for the advisor input)."""
    return _rows_by_variant(db)


def lifts_payload(db: Session) -> dict:
    return {"generated_at": common.generated_at(), "lifts": lift_summaries(db)}


def _pr_sets(db: Session, variant_id: int, session_ids: list[str]) -> dict[str, tuple]:
    """(weight, reps) of the set that produced each session's best e1RM."""
    if not session_ids:
        return {}
    best: dict[str, tuple[float, float | None, int | None]] = {}
    q = select(
        WorkoutSet.session_id, WorkoutSet.e1rm_kg, WorkoutSet.weight_kg, WorkoutSet.reps
    ).where(
        WorkoutSet.variant_id == variant_id,
        WorkoutSet.session_id.in_(session_ids),
        WorkoutSet.deleted_at.is_(None),
        WorkoutSet.e1rm_kg.is_not(None),
    )
    for sid, e1rm, w, r in db.execute(q):
        if sid not in best or e1rm > best[sid][0]:
            best[sid] = (e1rm, w, r)
    return {sid: (w, r) for sid, (_e, w, r) in best.items()}


def lift_detail(db: Session, variant_id: int, start: date | None, end: date | None) -> dict | None:
    """None if the variant is unknown or has no sessions."""
    cs.ensure_seeded(db, get_settings().engine_config_path)
    rows = _rows_by_variant(db, variant_id).get(variant_id)
    meta = _variant_meta(db, [variant_id]).get(variant_id)
    if not rows or meta is None:
        return None
    summary = _summary(*meta, rows, common.today(), _context(db))
    shown = [r for r in rows if (start is None or r.day >= start) and (end is None or r.day <= end)]
    points = [
        {
            "date": r.day.isoformat(),
            "session_id": r.session_id,
            "best_e1rm": num(r.e1rm),
            "top_set": (
                {"weight_kg": num(r.top_w), "reps": r.top_r} if r.top_w is not None else None
            ),
            "volume_kg": num(r.volume_kg),
            "sets": r.working_sets,
        }
        for r in shown
    ]
    e1rm_sets = _pr_sets(db, variant_id, [r.session_id for r in shown if r.e1rm_pr])
    prs: list[dict] = []
    for r in shown:
        d = r.day.isoformat()
        if r.e1rm_pr and r.e1rm:
            w, reps = e1rm_sets.get(r.session_id, (r.top_w, r.top_r))
            prs.append(
                {"date": d, "type": "e1rm", "value": num(r.e1rm), "weight_kg": num(w), "reps": reps}
            )
        if r.weight_pr and r.top_w:
            prs.append(
                {
                    "date": d,
                    "type": "weight",
                    "value": num(r.top_w),
                    "weight_kg": num(r.top_w),
                    "reps": r.top_r,
                }
            )
        for rp in r.rep_prs or []:
            prs.append(
                {
                    "date": d,
                    "type": "reps",
                    "value": rp["reps"],
                    "weight_kg": num(rp["weight_kg"]),
                    "reps": rp["reps"],
                }
            )
    return {
        "generated_at": common.generated_at(),
        "lift": summary,
        "points": points,
        "prs": prs,
    }
