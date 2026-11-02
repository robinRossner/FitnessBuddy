"""The compact JSON the advisor receives (kept under ~15k tokens, estimated as chars / 4).

Built from the stats/engine functions the dashboards already use (apps/fitness/analytics/*,
fitness/engine/*), never from raw tables where one of those exists. Sections (see the glossary in
prompt.py): why, today, week_start, config, program, lifts, readiness_14d, baselines, running,
recent_sessions, upcoming_plan, progression, schedule, rules, past_advisor_changes,
swap_candidates.

`fit_budget` trims the least valuable detail first when the document is over budget.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from apps.fitness.advisor import validator as V
from apps.fitness.advisor._common import resolve_settings
from apps.fitness.analytics import common, health, lifts, running
from apps.fitness.db.models import (
    AdvisorChange,
    AdvisorConsultation,
    DecisionLog,
    Exercise,
    ExerciseVariant,
    Outcome,
    TrainingSession,
    WorkoutSet,
)
from apps.fitness.engine.core import schedule_rules as SR
from apps.fitness.engine.core.config import EngineConfig
from apps.fitness.engine.core.ids import base_name_of
from apps.fitness.engine.core.program import Program, apply_overrides
from apps.fitness.engine.store import config_service as cs
from apps.fitness.engine.store import data as D
from apps.fitness.engine.store import schedule_edit as SE
from apps.fitness.engine.store import service
from apps.fitness.settings import Settings

log = logging.getLogger(__name__)

SCHEMA_VERSION = 1
WORKING = ("working", "failure")
INTERESTING_RULES = (
    "stall",
    "retest",
    "ladder",
    "multi_lift",
    "fatigue",
    "rung",
    "vdot",
    "quality",
    "step_back",
    "deload",
    "conflict",
    "anomaly",
    "stagnation",
    "reset",
    "volume",
    "downgrade",
    "config_update",
    "decrease",
    "pace_step",
    "hold",
)


def dumps(doc: Any) -> str:
    """Canonical compact JSON (sorted keys, no spaces): same input -> same bytes."""
    return json.dumps(doc, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def estimate_tokens(doc: Any) -> int:
    text = doc if isinstance(doc, str) else dumps(doc)
    return len(text) // 4 + 1


def _r(x: Any, nd: int = 1) -> Any:
    return round(x, nd) if isinstance(x, int | float) and not isinstance(x, bool) else x


# --------------------------------------------------------------------------- program + lifts


def _program(settings: Settings, cfg: EngineConfig) -> Program | None:
    try:
        prog = service.get_program(settings)
    except (OSError, ValueError):
        return None
    return apply_overrides(prog, cfg.exercise_overrides)


def lifts_section(
    db: Session, cfg: EngineConfig, program: Program | None, settings: Settings, today: date
) -> tuple[dict[str, list], dict[str, dict], dict[str, int]]:
    """(program per day, lifts per slug, slug -> variant id)."""
    if program is None:
        return {}, {}, {}
    gym = D.current_gym(db, settings.gyms_path)
    summaries = {s["variant_id"]: s for s in lifts.lift_summaries(db)}
    rows = lifts.rows_by_variant(db)
    first_week = common.week_start(today) - timedelta(weeks=11)
    prog_out: dict[str, list] = {}
    lifts_out: dict[str, dict] = {}
    variant_ids: dict[str, int] = {}
    for kind in sorted(program.days):
        entries = []
        for pe in program.exercises(kind):
            rr = pe.rep_range or cfg.strength.rep_range
            entries.append([pe.exercise, pe.variant, pe.sets, list(rr)])
            if pe.exercise in lifts_out:
                continue
            v = D.resolve_variant(db, pe.variant, gym)
            if v is None:
                lifts_out[pe.exercise] = {"name": pe.variant, "unresolved": True}
                continue
            variant_ids[pe.exercise] = v.id
            summ = summaries.get(v.id) or {}
            weekly: dict[date, float] = {}
            for r in rows.get(v.id, []):
                if r.e1rm and r.day >= first_week:
                    wk = common.week_start(r.day)
                    weekly[wk] = max(weekly.get(wk, 0.0), r.e1rm)
            lifts_out[pe.exercise] = {
                "name": pe.variant,
                "e1rm_12w": [[wk.isoformat(), _r(e)] for wk, e in sorted(weekly.items())],
                "latest": summ.get("latest_e1rm"),
                "best": summ.get("best_e1rm"),
                "trend_4w_pct": summ.get("trend_4w_pct"),
                "stalled": bool(summ.get("stalled")),
                "sessions": summ.get("sessions", 0),
            }
        prog_out[kind] = entries
    return prog_out, lifts_out, variant_ids


def swap_candidates(
    db: Session,
    slugs: list[str],
    program: Program | None,
    content: dict[str, Any],
    settings: Settings,
    limit: int = 5,
) -> dict[str, list[str]]:
    """Allowed swap targets per slug, using the validator's own rule (so the list is accurate)."""
    if program is None or not slugs:
        return {}
    ctx = V.build_context(db, content, program=program, settings=settings)
    variants = list(
        db.scalars(
            select(ExerciseVariant).where(
                ExerciseVariant.active.is_(True), ExerciseVariant.deleted_at.is_(None)
            )
        )
    )
    patterns = {
        e.slug: e.movement_pattern for e in db.scalars(select(Exercise))
    }  # same movement pattern ranks before same body region
    out: dict[str, list[str]] = {}
    for slug in slugs:
        ranked: dict[str, int] = {}
        for v in variants:
            base = base_name_of(v)
            if base in ranked:
                continue
            if V.validate_change(f"exercise_overrides.{slug}.variant", base, ctx).valid:
                same = patterns.get(v.exercise.slug) == patterns.get(slug)
                ranked[base] = 0 if same else 1
        names = sorted(ranked, key=lambda n: (ranked[n], n))[:limit]
        if names:
            out[slug] = names
    return out


# --------------------------------------------------------------------------- health + running


def _sleep_h(s: int | None) -> float | None:
    return round(s / 3600, 1) if s else None


def health_sections(db: Session) -> tuple[list, dict]:
    stats = health.health_stats(db, 14)
    days = [
        [
            d["date"],
            d["readiness"]["level"],
            _r(d["hrv_ms"]),
            _r(d["rhr"]),
            _sleep_h(d["sleep_s"]),
        ]
        for d in stats["days"]
    ]
    s = stats["summary"]
    baselines = {
        "hrv_7d": s["hrv_7d"],
        "hrv_28d": s["hrv_28d"],
        "rhr_7d": s["rhr_7d"],
        "rhr_28d": s["rhr_28d"],
        "sleep_7d_h": _sleep_h(s["sleep_7d_s"]),
        "sleep_28d_h": _sleep_h(s["sleep_28d_s"]),
        "bodyweight_kg": s["bodyweight_latest"],
        "bodyweight_change_4w_pct": s["bodyweight_change_4w_pct"],
    }
    return days, baselines


def running_section(db: Session) -> tuple[dict, dict[str, str]]:
    st = running.running_stats(db, 12)
    k = st["kpis"]
    easy = [
        [e["date"], e["type"], _r(e["distance_m"] / 1000, 1), e["efficiency"], e["decoupling_pct"]]
        for e in st["efficiency"][-8:]
    ]
    intervals = []
    for i in st["intervals"][-6:]:
        tgt, act = i["target_pace_s_per_km"], i["avg_work_pace_s_per_km"]
        dev = _r((act - tgt) / tgt * 100) if tgt and act else None  # negative = faster
        intervals.append(
            [i["date"], i["reps"], i["passed"], dev, i["hr_in_range"], i["recovery_ok"]]
        )
    types = {d["run_id"]: d["type"] for w in st["weekly"] for d in w["runs_detail"]}
    return (
        {
            "weekly_km_12w": [[w["week_start"], w["km"]] for w in st["weekly"]],
            "kpis": {
                "vdot": k["vdot_current"],
                "vdot_4w_ago": k["vdot_4w_ago"],
                "efficiency": k["efficiency_current"],
                "efficiency_4w_ago": k["efficiency_4w_ago"],
                "efficiency_change_pct": k["efficiency_change_pct"],
                "easy_hr_drift_flags_4w": k["easy_hr_drift_flags_4w"],
                "runs_this_week": k["runs_this_week"],
                "runs_planned_this_week": k["runs_planned_this_week"],
                "km_this_week": k["km_this_week"],
            },
            "easy_runs": easy,
            "intervals": intervals,
        },
        types,
    )


# --------------------------------------------------------------------------- sessions + plan


def _fmt_set(w: float | None, reps: int | None) -> str:
    return f"{w:g}x{reps}" if w is not None and reps is not None else f"{reps or ''}"


def recent_sessions(db: Session, today: date, run_types: dict[str, str]) -> list[dict]:
    since = datetime.combine(today - timedelta(days=13), datetime.min.time())
    out: list[dict] = []
    for s in db.scalars(
        select(TrainingSession)
        .where(TrainingSession.deleted_at.is_(None), TrainingSession.started_at_local >= since)
        .order_by(TrainingSession.started_at_local)
    ):
        blocks: dict[tuple[int, int], list[WorkoutSet]] = {}
        for ws in s.sets:
            if ws.deleted_at is None and ws.set_type in WORKING:
                blocks.setdefault((ws.exercise_order, ws.block_occurrence), []).append(ws)
        if not blocks:
            continue
        ex = []
        for _key, sets in sorted(blocks.items()):
            sets.sort(key=lambda w: w.set_index)
            ex.append([sets[0].variant.name, ",".join(_fmt_set(w.weight_kg, w.reps) for w in sets)])
        out.append(
            {"date": s.started_at_local.date().isoformat(), "kind": s.session_kind, "ex": ex}
        )
    for r in D.load_runs(db, today - timedelta(days=13), today):
        out.append(
            {
                "date": r.day.isoformat(),
                "kind": run_types.get(r.id, "run"),
                "km": _r(r.km, 1),
                "pace_s_km": _r(r.pace, 0),
                "hr": _r(r.avg_hr, 0),
            }
        )
    out.sort(key=lambda x: x["date"])
    return out


def upcoming_plan(db: Session, today: date, days: list[dict] | None = None) -> list[dict]:
    out = []
    for day in days if days is not None else service.plan_for_api(db, today, 7):
        for s in day["sessions"]:
            sess = s.get("session") or {}
            item: dict[str, Any] = {
                "date": day["date"],
                "kind": s.get("kind"),
                "title": s.get("title"),
            }
            if sess.get("type") == "lifting":
                item["ex"] = [e.get("name") for e in sess.get("exercises", [])]
            elif sess.get("distance_km") is not None:
                item["km"] = sess.get("distance_km")
            elif sess.get("type") == "intervals":
                item["structure"] = sess.get("structure")
            if (s.get("readiness") or {}).get("color"):
                item["readiness"] = s["readiness"]["color"]
            out.append(item)
    return out


def _latest(db: Session, since: datetime, **where: Any) -> list[DecisionLog]:
    q = select(DecisionLog).where(DecisionLog.created_at >= since)
    for k, v in where.items():
        q = q.where(getattr(DecisionLog, k) == v)
    return list(db.scalars(q.order_by(DecisionLog.id.desc()).limit(400)))


def progression_section(
    db: Session, days: list[dict], variant_names: dict[str, str]
) -> dict[str, Any]:
    """What the deterministic engine thinks of each lift and the running block right now, from
    the plan of the next 7 days and the latest decisions: progression state, effective rep
    range and note per lift, stall-ladder position, censored misses (14 days), the deload /
    retest window, the running phase, VDOT, the easy-pace calibration and the volume-cap
    reasoning."""
    lifts: dict[str, list] = {}
    run: dict[str, Any] = {}
    deload = None
    for day in days:
        for it in day["sessions"]:
            sess = it.get("session") or {}
            typ = sess.get("type")
            if typ == "lifting":
                if (sess.get("deload") or {}).get("active") and deload is None:
                    deload = sess["deload"]
                for ex in sess.get("exercises", []):
                    slug = ex.get("exercise")
                    if not slug or slug in lifts or not ex.get("progression_state"):
                        continue
                    rr = ex.get("effective_rep_range") or ex.get("rep_range") or []
                    lifts[slug] = [
                        ex["progression_state"],
                        "-".join(str(x) for x in rr),
                        (ex.get("progression_note") or "")[:140],
                    ]
            elif typ == "intervals" and "quality" not in run:
                st = sess.get("structure") or {}
                run["quality"] = {
                    "date": day["date"],
                    "phase": sess.get("phase"),
                    "phase_week": sess.get("phase_week"),
                    "session_type": st.get("session_type"),
                    "note": (sess.get("progression_note") or "")[:160],
                }
            elif typ == "long_run" and "long_run" not in run:
                run["long_run"] = {
                    "date": day["date"],
                    "km": sess.get("distance_km"),
                    "step_back": sess.get("step_back"),
                    "note": (sess.get("progression_note") or "")[:160],
                }
            elif typ == "easy_run" and "phase" not in run and sess.get("phase"):
                run["phase"] = [sess.get("phase"), sess.get("phase_week")]
    since = datetime.now(UTC) - timedelta(days=14)
    ladder: dict[str, list] = {}
    censored: list[list] = []
    seen_c: set[tuple] = set()
    for r in _latest(db, since, kind="progression", subject_type="variant"):
        name = variant_names.get(r.subject_id or "", r.subject_id)
        inp = r.inputs or {}
        if name not in ladder and "ladder_position" in inp:
            ladder[name] = [
                inp.get("ladder_position"),
                inp.get("stall_counter"),
                inp.get("stall_n"),
            ]
        for d, why in inp.get("censored") or []:
            if (d, name) not in seen_c:
                seen_c.add((d, name))
                censored.append([d, name, why])
    vol = next(
        (
            r.output
            for r in _latest(db, since, kind="running", subject_type="week")
            if (r.output or {}).get("rule") == "running:weekly_volume_cap"
        ),
        None,
    )
    vdot = next(
        (
            {**(r.output or {})["vdot_record"], "reason": r.rationale}
            for r in _latest(db, datetime.now(UTC) - timedelta(days=60), kind="running")
            if (r.output or {}).get("vdot_record")
        ),
        None,
    )
    if vol:
        run["volume"] = {
            k: vol.get(k)
            for k in ("cap_km", "mean_km_4w", "target_km", "single_run_cap_km", "reasoning")
        }
    if vdot:
        run["vdot"] = vdot
    easy = next(
        (
            r.output
            for r in _latest(db, since, kind="running", subject_type="week")
            if (r.output or {}).get("rule") == "running:easy_pace_calibration"
        ),
        None,
    )
    if easy:
        run["easy_pace"] = {
            k: easy.get(k) for k in ("basis", "vdot", "quality_vdot", "reason", "capped")
        } | {"range": (easy.get("range") or {}).get("label")}
    return {
        "lifts": lifts,
        "lift_columns": ["state", "effective rep range", "note"],
        "stall_ladder": ladder,
        "stall_ladder_columns": ["ladder step taken", "clean sessions w/o progress", "N"],
        "censored_misses_14d": sorted(censored)[-20:],
        "deload": deload,
        "running": run,
    }


def schedule_section(db: Session, cfg: EngineConfig, today: date) -> dict[str, Any]:
    """The current and next week as planned sessions (past ones with their outcome), plus the
    rules a `schedule_changes` entry must obey, so Claude can propose valid moves."""
    sc = cfg.schedule_cfg
    ws = today - timedelta(days=today.weekday())
    weeks = []
    missed = []
    for i in range(sc.horizon_weeks):
        w = ws + timedelta(weeks=i)
        sessions = []
        for r in SE.load_rows(db, w):
            if not (w <= r.day < w + timedelta(days=7)):
                continue
            km = SE.prescribed_km(r)
            sessions.append(
                [
                    r.day.isoformat(),
                    r.kind,
                    r.status,
                    _r(km, 1),
                    r.moved_from.isoformat() if r.moved_from else None,
                ]
            )
            if r.status == "skipped" and r.day < today and i == 0:
                missed.append([r.day.isoformat(), r.kind])
        weeks.append({"week_start": w.isoformat(), "sessions": sessions})
    rules = SR.Rules.from_config(cfg)
    return {
        "columns": ["date", "kind", "status", "km", "moved_from"],
        "weeks": weeks,
        "missed_this_week": missed,
        "run_km_left_under_cap": SE.volume_room(db, cfg, today, ws),
        "rules": {
            "hard_kinds": list(sc.hard_kinds),
            "max_hard_per_day": sc.max_hard_per_day,
            "no_consecutive_hard_days": sc.no_consecutive_hard_days,
            "max_sessions_per_day": sc.max_sessions_per_day,
            "min_days_lower_to_intervals": rules.min_gap,
            "shorten_max_pct": sc.shorten_max_pct,
            "kind_load": sc.kind_load,
            "makeup_priority": list(sc.makeup_priority),
        },
    }


# --------------------------------------------------------------------------- rules + history


def rules_section(db: Session, today: date, variant_names: dict[str, str], limit: int = 30) -> list:
    since = datetime.now(UTC) - timedelta(days=14)
    rows = db.scalars(
        select(DecisionLog).where(DecisionLog.created_at >= since).order_by(DecisionLog.id.desc())
    )
    seen: set[tuple] = set()
    picked: list[tuple[int, DecisionLog]] = []
    for r in rows:
        rule = (r.output or {}).get("rule", r.kind)
        key = (r.kind, r.subject_type, r.subject_id, rule)
        if key in seen:
            continue
        seen.add(key)
        score = 0 if any(w in str(rule) for w in INTERESTING_RULES) else 1
        picked.append((score, r))
    picked.sort(key=lambda p: (p[0], -p[1].id))
    out = []
    for _score, r in picked[:limit]:
        subj = (
            variant_names.get(r.subject_id or "", r.subject_id)
            if r.subject_type == "variant"
            else r.subject_id
        )
        out.append(
            [
                r.created_at.date().isoformat(),
                r.kind,
                (r.output or {}).get("rule", r.kind),
                subj,
                (r.rationale or "")[:160],
            ]
        )
    return out


def past_changes_section(db: Session, limit: int = 12) -> list:
    out = []
    rows = db.execute(
        select(AdvisorChange, AdvisorConsultation)
        .join(AdvisorConsultation, AdvisorChange.consultation_id == AdvisorConsultation.id)
        .where(AdvisorChange.status.in_(("applied", "rejected")))
        .order_by(AdvisorChange.id.desc())
        .limit(limit)
    )
    for ch, c in rows:
        outcome = None
        if ch.applied_config_version_id:
            o = db.scalar(
                select(Outcome).where(Outcome.config_version_id == ch.applied_config_version_id)
            )
            if o is not None:
                outcome = {
                    "verdict": o.verdict,
                    "notes": o.notes,
                    "after": (o.metrics or {}).get("after"),
                }
        out.append(
            {
                "date": (ch.applied_at or c.created_at).date().isoformat(),
                "param": ch.param,
                "from": ch.old_value,
                "to": ch.new_value,
                "status": ch.status,
                "outcome": outcome,
            }
        )
    return out


# --------------------------------------------------------------------------- focus (ask)


def focus_section(db: Session, ref: dict | None, today: date) -> dict | None:
    if not ref or not ref.get("kind") or not ref.get("id"):
        return None
    kind, rid = ref["kind"], str(ref["id"])
    if kind == "lift":
        detail = lifts.lift_detail(db, int(rid), today - timedelta(weeks=26), today)
        if detail is None:
            return {"kind": "lift", "id": rid, "note": "no history for this lift"}
        return {
            "kind": "lift",
            "name": detail["lift"]["name"],
            "sessions": [
                [p["date"], p["best_e1rm"], p["top_set"], p["sets"]] for p in detail["points"][-14:]
            ],
            "prs": [[p["date"], p["type"], p["value"]] for p in detail["prs"][-5:]],
        }
    if kind == "run":
        from apps.fitness.analytics import run_analysis

        a = run_analysis.run_analysis(db, rid)
        if a is None:
            return {"kind": "run", "id": rid, "note": "run not found"}
        run = a["run"]
        return {
            "kind": "run",
            "run": {
                k: run.get(k) for k in ("date", "type", "distance_m", "avg_hr", "avg_pace_s_per_km")
            },
            "verdict": a["verdict"],
            "laps": [
                [
                    lp["kind"],
                    _r(lp["distance_m"], 0),
                    lp["avg_pace_s_per_km"],
                    lp["avg_hr"],
                ]
                for lp in a["laps"]
            ][:24],
        }
    if kind == "week":
        ws = date.fromisoformat(rid)
        days = service.plan_for_api(db, ws, 7)
        return {
            "kind": "week",
            "week_start": ws.isoformat(),
            "plan": [
                [d["date"], s.get("kind"), s.get("status")] for d in days for s in d["sessions"]
            ],
        }
    return None


# --------------------------------------------------------------------------- assemble


def build_input(
    db: Session,
    *,
    trigger: dict[str, Any],
    question: str | None,
    settings: Settings | None = None,
) -> dict[str, Any]:
    s = resolve_settings(settings)
    cs.ensure_seeded(db, s.engine_config_path)
    cfg, row = cs.load_active(db)
    today = common.today()
    program = _program(s, cfg)

    prog_out, lifts_out, variant_ids = lifts_section(db, cfg, program, s, today)
    readiness, baselines = health_sections(db)
    run_doc, run_types = running_section(db)
    variant_names = {str(vid): lifts_out[slug]["name"] for slug, vid in variant_ids.items()}
    plan_days = service.plan_for_api(db, today, 7)

    ctx = V.build_context(db, row.content, program=program, settings=s)
    params = {
        spec.template: ctx.effective(spec.template)
        for spec in V.SPECS
        if "{slug}" not in spec.template
    }
    stalled = [slug for slug, d in lifts_out.items() if d.get("stalled")]
    ref = trigger.get("ref") or {}
    focus_slugs = list(stalled)
    if ref.get("kind") == "lift" and ref.get("id") is not None:
        focus_slugs += [slug for slug, vid in variant_ids.items() if str(vid) == str(ref["id"])]

    why = {
        "type": trigger.get("type"),
        "title": trigger.get("title"),
        "detail": trigger.get("detail"),
        "items": trigger.get("items") or [],
        "question": question,
        "focus": focus_section(db, ref, today),
    }
    doc: dict[str, Any] = {
        "schema": SCHEMA_VERSION,
        "why": {k: v for k, v in why.items() if v not in (None, [], "")},
        "today": today.isoformat(),
        "week_start": common.week_start(today).isoformat(),
        "config": {
            "version": row.version,
            "approval_mode": cfg.advisor_cfg.approval_mode,
            "params": params,
            "exercise_overrides": cfg.exercise_overrides or {},
        },
        "program": prog_out,
        "lifts": lifts_out,
        "readiness_14d": readiness,
        "baselines": baselines,
        "running": run_doc,
        "recent_sessions": recent_sessions(db, today, run_types),
        "upcoming_plan": upcoming_plan(db, today, plan_days),
        "progression": progression_section(db, plan_days, variant_names),
        "schedule": schedule_section(db, cfg, today),
        "rules": rules_section(db, today, variant_names),
        "past_advisor_changes": past_changes_section(db),
        "swap_candidates": swap_candidates(db, sorted(set(focus_slugs)), program, row.content, s),
    }
    return fit_budget(doc, cfg.advisor_cfg.input_budget_tokens)


def fit_budget(doc: dict[str, Any], budget_tokens: int) -> dict[str, Any]:
    """Trim the least valuable detail until the document is under budget (chars / 4)."""
    steps = [
        lambda d: d.__setitem__("rules", d["rules"][:12]),
        lambda d: d.__setitem__(
            "recent_sessions",
            [dict(x, ex=x["ex"][:6]) if "ex" in x else x for x in d["recent_sessions"]],
        ),
        lambda d: d["running"].__setitem__("easy_runs", d["running"]["easy_runs"][-4:]),
        lambda d: d.__setitem__("rules", d["rules"][:5]),
        lambda d: d["progression"].__setitem__(
            "censored_misses_14d", d["progression"]["censored_misses_14d"][-6:]
        ),
        lambda d: d.__setitem__("past_advisor_changes", d["past_advisor_changes"][:5]),
        lambda d: [
            v.__setitem__("e1rm_12w", v["e1rm_12w"][-6:])
            for v in d["lifts"].values()
            if "e1rm_12w" in v
        ],
        lambda d: d.__setitem__(
            "recent_sessions", [x for x in d["recent_sessions"] if "ex" not in x]
        ),
        lambda d: d.__setitem__("rules", []),
    ]
    for step in steps:
        if estimate_tokens(doc) <= budget_tokens:
            break
        step(doc)
    if estimate_tokens(doc) > budget_tokens:
        log.warning("advisor input is %d tokens (budget %d)", estimate_tokens(doc), budget_tokens)
    return json.loads(dumps(doc))


def user_message(doc: dict[str, Any]) -> str:
    """The volatile part of the request (after the cached system prompt)."""
    q = (doc.get("why") or {}).get("question")
    head = "Consultation input follows as JSON."
    if q:
        head += " The athlete asked a question: answer it first."
    return f"{head}\n\n<input>\n{dumps(doc)}\n</input>"
