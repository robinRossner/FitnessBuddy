"""Markdown import report + short console summary."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from apps.fitness.db.models import Exercise, ExerciseSessionStats, ExerciseVariant, Gym
from apps.fitness.db.types import EquipmentType, LoadConvention, SetType
from apps.fitness.importers.strong.importer import ImportResult

MAIN_PATTERNS = {
    "horizontal_push",
    "vertical_push",
    "horizontal_pull",
    "vertical_pull",
    "squat",
    "hinge",
    "lunge",
}
JUMP_PCT = 30
ODD_HOUR_START, ODD_HOUR_END = 5, 23  # starts before 05:00 or from 23:00 are flagged
LIST_LIMIT = 40


@dataclass
class VariantSummary:
    name: str
    exercise: str
    pattern: str
    equipment: str
    convention: str
    sessions: int
    first: date
    last: date
    first_e1rm: float | None
    latest_e1rm: float | None
    best_e1rm: float | None
    best_e1rm_date: date | None
    basis: str | None
    weights: list[float]
    active: bool = True
    gym: str | None = None


def variant_summaries(db: Session) -> list[VariantSummary]:
    rows = db.execute(
        select(ExerciseSessionStats, ExerciseVariant, Exercise)
        .join(ExerciseVariant, ExerciseSessionStats.variant_id == ExerciseVariant.id)
        .join(Exercise, ExerciseVariant.exercise_id == Exercise.id)
        .order_by(ExerciseSessionStats.session_date)
    ).all()
    gym_names = dict(db.execute(select(Gym.id, Gym.name)).all())
    grouped: dict[int, list[tuple[ExerciseSessionStats, ExerciseVariant, Exercise]]] = defaultdict(
        list
    )
    for st, v, ex in rows:
        grouped[v.id].append((st, v, ex))
    out: list[VariantSummary] = []
    for items in grouped.values():
        _, v, ex = items[0]
        e1 = [
            (st.session_date, st.best_e1rm_kg, st.e1rm_basis)
            for st, _, _ in items
            if st.best_e1rm_kg
        ]
        best = max(e1, key=lambda x: x[1]) if e1 else None
        out.append(
            VariantSummary(
                name=v.name,
                exercise=ex.slug,
                pattern=ex.movement_pattern,
                equipment=v.equipment_type,
                convention=v.load_convention,
                sessions=len(items),
                first=items[0][0].session_date,
                last=items[-1][0].session_date,
                first_e1rm=e1[0][1] if e1 else None,
                latest_e1rm=e1[-1][1] if e1 else None,
                best_e1rm=best[1] if best else None,
                best_e1rm_date=best[0] if best else None,
                basis=e1[0][2] if e1 else None,
                weights=[st.top_set_weight_kg for st, _, _ in items if st.top_set_weight_kg],
                active=v.active,
                gym=gym_names.get(v.gym_id),
            )
        )
    out.sort(key=lambda s: (-s.sessions, s.name))
    return out


def weight_jumps(db: Session) -> list[tuple[str, date, float, date, float, float]]:
    """Session-to-session top-set weight changes > JUMP_PCT % per variant."""
    rows = db.execute(
        select(ExerciseSessionStats, ExerciseVariant.name)
        .join(ExerciseVariant, ExerciseSessionStats.variant_id == ExerciseVariant.id)
        .where(ExerciseVariant.active.is_(True))  # inactive variants are not anomalies
        .order_by(ExerciseSessionStats.variant_id, ExerciseSessionStats.session_date)
    ).all()
    out = []
    prev: dict[str, tuple[date, float]] = {}
    for st, name in rows:
        w = st.top_set_weight_kg
        if not w:
            continue
        if name in prev:
            d0, w0 = prev[name]
            pct = (w - w0) / w0 * 100
            if abs(pct) > JUMP_PCT:
                out.append((name, d0, w0, st.session_date, w, round(pct)))
        prev[name] = (st.session_date, w)
    return out


def _fmt(v: float | None, nd: int = 1) -> str:
    return "–" if v is None else f"{v:.{nd}f}"


def _table(header: list[str], rows: list[list[str]]) -> list[str]:
    out = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    out += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return out


def build_report(db: Session, res: ImportResult, generated_at: datetime) -> str:
    L: list[str] = []
    mode = "APPLIED" if res.apply else "DRY RUN (nothing written)"
    L += [f"# Strong import report – {generated_at:%Y-%m-%d %H:%M}", "", f"Mode: **{mode}**", ""]

    if res.error:
        L += [f"**Import aborted:** {res.error}", ""]
        L += [f"- {e}" for e in res.mapping_errors]
        return "\n".join(L) + "\n"

    # ---------------------------------------------------------------- summary
    L += ["## Summary", ""]
    L += _table(
        ["file", "strong?", "rows", "workouts", "workouts used", "first", "last", "delimiter"],
        [
            [
                f.name,
                "yes" if f.is_strong else "no (ignored)",
                f.rows_read,
                f.workouts,
                f.workouts_used,
                f"{f.min_date:%Y-%m-%d}" if f.min_date else "–",
                f"{f.max_date:%Y-%m-%d}" if f.max_date else "–",
                repr(f.delimiter),
            ]
            for f in res.files
        ],
    )
    L += [
        "",
        f"- Rows read: **{res.rows_read}**, imported: **{res.rows_imported}**, "
        f"skipped: **{len(res.skipped)}**",
        f"- Sessions: **{res.sessions_total}** (created {res.sessions_created}, updated "
        f"{res.sessions_updated}, unchanged {res.sessions_unchanged}, kept deleted "
        f"{res.sessions_tombstoned_skipped})",
        f"- Sets: created {res.sets_created}, updated {res.sets_updated}, unchanged "
        f"{res.sets_unchanged}, tombstoned {res.sets_tombstoned}",
        f"- Date range: {res.date_min} → {res.date_max}",
        f"- Workouts present in several files: {res.overlapping_workouts} "
        f"({res.overlapping_differing} with differing content; newest export used)",
        f"- exercise_session_stats rows: {res.stats_rows}",
        f"- Sets moved to another variant (gym change): {res.sets_moved}; orphaned variants "
        f"removed: {res.variants_removed}",
        "",
    ]
    if res.skip_reasons:
        L += ["Skipped rows by reason:", ""]
        L += _table(["reason", "rows"], [[k, v] for k, v in res.skip_reasons.most_common()])
        L += [""]

    # ---------------------------------------------------------------- mapping
    L += ["## Exercise mapping", ""]
    if res.unmapped_names:
        L += ["**Unmapped names (sets skipped)** – fill in `strong_mapping.csv`:", ""]
        L += _table(["strong name", "rows"], [[k, v] for k, v in res.unmapped_names.most_common()])
        L += [""]
    else:
        L += ["No unmapped names.", ""]
    auto = [res.mapping[n] for n in res.auto_names if n in res.mapping and res.mapping[n].active]
    if auto:

        def conf(m) -> int:
            n = m.notes.upper()
            return 0 if "LOW CONFIDENCE" in n else 1 if "QUESTION" in n or "MEDIUM" in n else 2

        auto.sort(key=lambda m: (conf(m), m.strong_name.casefold()))
        L += [
            f"**{len(auto)} auto-mapped names to review** (set `status=confirmed` once checked; "
            "least certain first):",
            "",
        ]
        L += _table(
            ["strong name", "canonical", "variant", "equipment", "load", "sets", "notes"],
            [
                [
                    m.strong_name,
                    m.canonical_exercise,
                    m.variant_name,
                    m.equipment_type,
                    m.load_convention,
                    res.auto_names[m.strong_name],
                    m.notes,
                ]
                for m in auto
            ],
        )
        L += [""]

    summaries = variant_summaries(db)

    # ---------------------------------------------------------------- gyms + decisions
    L += ["## Gyms", ""]
    L += [
        "Gym-bound variants (machine / cable / smith / plate-loaded) exist once per gym; periods "
        "come from `data/catalog/gyms.yaml` (edit and re-import to change them).",
        "",
    ]
    L += _table(
        ["gym", "from", "note", "sessions"],
        [
            [name, start or "(beginning)", note or "", res.sessions_by_gym.get(name, 0)]
            for name, start, note in res.gym_ranges
        ],
    )
    per_gym = Counter(s.gym or "(generic)" for s in summaries)
    listing = ", ".join(f"{k}: {v}" for k, v in sorted(per_gym.items()))
    L += ["", f"Variants with data per gym: {listing}", ""]
    L += ["## Applied decisions", ""]
    L += [
        "- Dumbbell weights are **per hand** (confirmed). Inactive variants "
        + (", ".join(sorted(n for n, m in res.mapping.items() if not m.active)) or "(none)")
        + " keep their history but are left out of the review and anomaly sections.",
        "- Bodyweight comes from the `body_measurements` log (latest on/before the session date, "
        "else the earliest after, else `user.bodyweight_kg` from the engine config). Pull-ups, "
        "dips etc. use bodyweight + added weight, assisted/band pull-ups bodyweight - assistance.",
        f"- Sessions with an inferred kind (generic names): {sum(res.kinds_inferred.values())} "
        + (str(dict(res.kinds_inferred)) if res.kinds_inferred else ""),
        "",
    ]

    # ---------------------------------------------------------------- anomalies
    L += ["## Anomalies", ""]
    variant_by_name = {m.strong_name: m for m in res.mapping.values()}
    inactive = {n for n, m in res.mapping.items() if not m.active}
    rows = [r for wk in res.workouts for r in wk.rows if r.exercise_name not in inactive]

    jumps = weight_jumps(db)
    L += [
        f"### Top-set weight jumps > {JUMP_PCT} % between consecutive sessions ({len(jumps)})",
        "",
    ]
    if jumps:
        months = Counter(f"{j[3]:%Y-%m}" for j in jumps)
        L += ["By month: " + ", ".join(f"{m}: {c}" for m, c in sorted(months.items())), ""]
        L += _table(
            ["variant", "from", "kg", "to", "kg", "change %"],
            [
                [j[0], j[1], f"{j[2]:g}", j[3], f"{j[4]:g}", f"{j[5]:+d}"]
                for j in jumps[:LIST_LIMIT]
            ],
        )
        if len(jumps) > LIST_LIMIT:
            L += [f"… and {len(jumps) - LIST_LIMIT} more"]
        L += [""]

    many_reps = [r for r in rows if r.reps and r.reps > 30]
    L += [f"### Sets with > 30 reps: {len(many_reps)}", ""]
    L += [f"- {r.start_local} {r.exercise_name}: {r.reps}" for r in many_reps[:LIST_LIMIT]]

    def is_bw(r) -> bool:
        m = variant_by_name.get(r.exercise_name)
        return m is not None and (
            m.equipment_type == EquipmentType.BODYWEIGHT
            or m.load_convention == LoadConvention.ASSISTANCE
        )

    zero = Counter(
        r.exercise_name
        for r in rows
        if not r.weight_kg and not r.is_timed_hold and not r.is_cardio and not is_bw(r)
    )
    L += ["", f"### Zero-weight sets on non-bodyweight variants: {sum(zero.values())}", ""]
    L += [f"- {k}: {v}" for k, v in zero.most_common()]

    holds = Counter(r.exercise_name for r in rows if r.is_timed_hold)
    L += ["", f"### Timed holds (reps empty, duration stored): {sum(holds.values())}", ""]
    L += [f"- {k}: {v}" for k, v in holds.most_common()]

    marked = [r for r in rows if r.set_type != SetType.WORKING]
    L += ["", f"### Marked sets (failure / warm-up / drop): {len(marked)}", ""]
    L += [
        f"- {r.start_local} {r.exercise_name} set {r.set_index} → {r.set_type}"
        for r in marked[:LIST_LIMIT]
    ]

    per_day = Counter(wk.start_local.date() for wk in res.workouts)
    multi = sorted(d for d, c in per_day.items() if c > 1)
    L += ["", f"### Days with more than one workout: {len(multi)}", ""]
    for d in multi[:LIST_LIMIT]:
        names = [
            f"{w.start_local:%H:%M} {w.name}" for w in res.workouts if w.start_local.date() == d
        ]
        L += [f"- {d}: " + "; ".join(names)]

    odd = [
        wk
        for wk in res.workouts
        if wk.start_local.hour < ODD_HOUR_START or wk.start_local.hour >= ODD_HOUR_END
    ]
    L += ["", f"### Odd start times (before 05:00 or after 23:00): {len(odd)}", ""]
    L += [f"- {wk.start_local} {wk.name} ({_dur(wk.duration_s)})" for wk in odd]

    L += ["", f"### DST-ambiguous / nonexistent local start times: {len(res.dst_flags)}", ""]
    L += [f"- {d} ({flag}; resolved with fold=0)" for d, flag in res.dst_flags]

    odd_w = Counter(
        (r.exercise_name, r.weight_kg) for r in rows if r.weight_kg and (r.weight_kg * 4) % 1
    )
    L += [
        "",
        f"### Weights not a multiple of 0.25 kg (lb conversion / typo?): {sum(odd_w.values())}",
        "",
    ]
    L += [f"- {n}: {w:g} kg × {c}" for (n, w), c in odd_w.most_common(LIST_LIMIT)]

    res.anomaly_counts = {
        "weight jumps": len(jumps),
        "sets > 30 reps": len(many_reps),
        "zero-weight sets": sum(zero.values()),
        "odd start times": len(odd),
        "DST-ambiguous starts": len(res.dst_flags),
        "odd weights": sum(odd_w.values()),
    }
    corrected = {start for start, _, _ in res.duration_corrected}
    weird_dur = [
        wk
        for wk in res.workouts
        if wk.start_local not in corrected
        and (not wk.duration_s or wk.duration_s < 600 or wk.duration_s > 3 * 3600)
    ]
    res.anomaly_counts["odd durations"] = len(weird_dur)
    L += ["", f"### Workout duration missing, < 10 min or > 3 h: {len(weird_dur)}", ""]
    L += [f"- {wk.start_local} {wk.name}: {_dur(wk.duration_s)}" for wk in weird_dur[:LIST_LIMIT]]

    L += [
        "",
        f"### Durations corrected (> {_dur(res.max_duration_s)}, stored as NULL): "
        f"{len(res.duration_corrected)}",
        "",
    ]
    L += [f"- {d} {n}: was {_dur(sec)}" for d, n, sec in res.duration_corrected]

    issues = Counter(issue for _, issue in res.row_issues)
    if issues:
        L += ["", "### Other row issues", ""]
        L += [f"- {k}: {v}" for k, v in issues.most_common()]

    # ---------------------------------------------------------------- ladders
    L += ["", "## Inferred weight ladders (machine stack / cable)", ""]
    L += [
        "Distinct historical weights, used by `next_load` as rungs. Edit `weight_ladder` on the "
        "variant to correct (edited ladders are not overwritten). Mixed-machine histories give "
        "messy ladders.",
        "",
    ]
    L += [f"- {n}: {', '.join(f'{w:g}' for w in lad)}" for n, lad in sorted(res.ladders.items())]

    # ---------------------------------------------------------------- per variant
    def vrow(s: VariantSummary) -> list[str]:
        unit = " (added)" if s.basis == "added_weight" else ""
        return [
            s.name,
            s.equipment,
            s.sessions,
            s.first,
            s.last,
            _fmt(s.first_e1rm),
            _fmt(s.latest_e1rm),
            f"{_fmt(s.best_e1rm)}{unit}",
            s.best_e1rm_date or "–",
        ]

    hdr = [
        "variant",
        "equipment",
        "sessions",
        "first",
        "last",
        "first e1RM",
        "latest e1RM",
        "best e1RM",
        "best on",
    ]
    main = [s for s in summaries if s.pattern in MAIN_PATTERNS]
    other = [s for s in summaries if s.pattern not in MAIN_PATTERNS]
    L += ["", "## Per-variant summary (e1RM = Epley, best working set ≤ 15 reps, kg)", ""]
    L += ["### Main lifts (compound patterns)", "", *_table(hdr, [vrow(s) for s in main])]
    L += ["", "### Other variants", "", *_table(hdr, [vrow(s) for s in other])]
    return "\n".join(L) + "\n"


def _dur(seconds: int | None) -> str:
    if not seconds:
        return "no duration"
    h, m = divmod(seconds // 60, 60)
    return f"{h}h {m}m" if h else f"{m}m"


def write_report(text: str, reports_dir: Path, generated_at: datetime) -> Path:
    reports_dir.mkdir(parents=True, exist_ok=True)
    path = reports_dir / f"strong_import_{generated_at:%Y%m%d_%H%M%S}.md"
    path.write_text(text, encoding="utf-8")
    return path


def console_summary(res: ImportResult) -> str:
    if res.error:
        return "ERROR: " + res.error + "\n" + "\n".join(f"  - {e}" for e in res.mapping_errors)
    lines = [
        f"mode: {'apply' if res.apply else 'dry-run'}",
        f"files: {len(res.files)} ({sum(f.is_strong for f in res.files)} Strong exports)",
        f"rows read {res.rows_read} | imported {res.rows_imported} | skipped {len(res.skipped)}",
        f"sessions {res.sessions_total} (new {res.sessions_created}, "
        f"updated {res.sessions_updated},"
        f" unchanged {res.sessions_unchanged})",
        f"sets new {res.sets_created}, updated {res.sets_updated}, unchanged {res.sets_unchanged},"
        f" tombstoned {res.sets_tombstoned}",
        f"date range {res.date_min} -> {res.date_max}",
        f"unmapped names: {len(res.unmapped_names)} | "
        f"auto-mapped (to review): {len(res.auto_names)}",
        f"gyms: {dict(res.sessions_by_gym)} | sets moved {res.sets_moved} | "
        f"variants removed {res.variants_removed} | durations corrected "
        f"{len(res.duration_corrected)}",
    ]
    if res.anomaly_counts:
        lines.append(
            f"anomalies remaining: {sum(res.anomaly_counts.values())} {res.anomaly_counts}"
        )
    for reason, n in res.skip_reasons.most_common():
        lines.append(f"  skipped: {reason}: {n}")
    return "\n".join(lines)
