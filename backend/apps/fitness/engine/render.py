"""Plain-text rendering of plans (CLI)."""

from __future__ import annotations

from typing import Any

from apps.fitness.engine.pace import fmt_pace
from apps.fitness.engine.planner import DayPlan, WeekPlan


def _fmt_set(s: dict[str, Any]) -> str:
    w, r, d = s.get("target_weight"), s.get("target_reps"), s.get("target_duration_s")
    if d is not None:
        return f"{d:g}s"
    if w is None:
        return f"?x{r}"
    return f"{w:g}x{r}"


def summarize_day(d: DayPlan, *, detail: bool = False) -> list[str]:
    head = f"{d.day:%a %m-%d}  {d.title}"
    if d.readiness and d.readiness.get("color"):
        head += f"  [readiness {d.readiness['color']}: {d.readiness['reason']}]"
    lines = [head]
    pres = d.prescription
    t = pres.get("type")
    if t == "lifting":
        dl = pres.get("deload") or {}
        if dl.get("active"):
            lines.append(f"    deload {dl.get('from')} .. {dl.get('until')} ({dl.get('reason')})")
        for ex in pres["exercises"]:
            sets = " ".join(_fmt_set(s) for s in ex["sets"])
            state = ex.get("progression_state")
            rr = ex.get("effective_rep_range")
            tail = f"   [{state}" + (f" {rr[0]}-{rr[1]}" if rr else "") + "]" if state else ""
            lines.append(f"    {ex['name']}: {sets}{tail}")
            if detail and ex.get("progression_note"):
                lines.append(f"        {ex['progression_note']}")
    elif t == "intervals":
        st = pres["structure"]
        w = st["work"]
        rec = st.get("recovery") or {}
        rec_txt = (
            f"rec {rec['duration_s']:g} s jog"
            if rec.get("duration_s")
            else f"rec {rec['distance_m']:g} m jog"
            if rec.get("distance_m")
            else "no recovery"
        )
        lines.append(
            f"    [{pres.get('phase')} wk {pres.get('phase_week')}] "
            f"{w['reps']}x{w['distance_m']:g} m @ {w['pace_label']}/km, {rec_txt}, "
            f"total {pres['total_km']:g} km"
        )
        if pres.get("progression_note"):
            lines.append(f"        {pres['progression_note']}")
    elif t in ("easy_run", "long_run"):
        p = pres["pace"]
        extra = ""
        strides = (pres.get("structure") or {}).get("strides")
        if strides:
            extra = f" + {strides['reps']}x{strides['duration_s']:g} s strides"
        lines.append(
            f"    {pres['distance_km']:g} km{extra}, HR {pres['hr']['low']}-{pres['hr']['high']}, "
            f"pace {p['label']}"
        )
        if pres.get("progression_note") and t == "long_run":
            lines.append(f"        {pres['progression_note']}")
    for a in d.adjustments:
        lines.append(f"    ADJUSTED: {a}")
    for n in d.notes:
        lines.append(f"    note: {n}")
    return lines


def render_week(plan: WeekPlan, *, detail: bool = False, decisions: bool = True) -> str:
    z = plan.zones
    out = [
        f"week of {plan.week_start}  (as of {plan.today}, config {plan.config_version})",
        f"readiness today: {plan.readiness.color}{'' if plan.readiness.known else ' (unknown)'} - {plan.readiness.reason}",  # noqa: E501
        f"HR: max {z['max_hr']}, LTHR {z['lthr']}, easy {z['easy']['low']}-{z['easy']['high']}, "
        f"intervals {z['intervals']['low']}-{z['intervals']['high']}",
        f"pace model: {plan.pace}",
    ]
    for d in plan.days:
        out.extend(summarize_day(d, detail=detail))
    if plan.volume.get("planned_km"):
        v = plan.volume
        out.append(
            f"run volume: {v['planned_km']} (cap {v['cap_km']}, done {v['done_km_this_week']})"
        )
        if v.get("reasoning"):
            out.append(f"  {v['reasoning']}")
    if plan.running:
        ph = plan.running.get("phase") or {}
        out.append(
            f"running: VDOT {plan.running.get('vdot')}, phase {ph.get('phase')} week "
            f"{ph.get('phase_week')}/{ph.get('phase_weeks')} (cycle {ph.get('cycle')})"
        )
    if decisions:
        out.append("decisions:")
        for r in plan.decisions:
            out.append(f"  - [{r.kind}] {r.rule}: {r.rationale}")
        for t in plan.triggers:
            out.append(f"  ! advisor trigger {t.type}: {t.dedupe_key}")
    return "\n".join(out)


__all__ = ["fmt_pace", "render_week", "summarize_day"]
