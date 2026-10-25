"""Plain-text rendering of plans (CLI)."""

from __future__ import annotations

from typing import Any

from app.engine.pace import fmt_pace
from app.engine.planner import DayPlan, WeekPlan


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
        for ex in pres["exercises"]:
            sets = " ".join(_fmt_set(s) for s in ex["sets"])
            lines.append(f"    {ex['name']}: {sets}" + (f"   ({ex['rule']})" if detail else ""))
    elif t == "intervals":
        w = pres["structure"]["work"]
        lines.append(
            f"    {w['reps']}x{w['distance_m']:g} m @ {w['pace_label']}/km, HR {w['hr']['low']}-{w['hr']['high']}, "  # noqa: E501
            f"rec {pres['structure']['recovery']['distance_m']:g} m jog, total {pres['total_km']:g} km"  # noqa: E501
        )
    elif t in ("easy_run", "long_run"):
        p = pres["pace"]
        lines.append(
            f"    {pres['distance_km']:g} km, HR {pres['hr']['low']}-{pres['hr']['high']}, pace {p['label']}"  # noqa: E501
        )
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
    if decisions:
        out.append("decisions:")
        for r in plan.decisions:
            out.append(f"  - [{r.kind}] {r.rule}: {r.rationale}")
        for t in plan.triggers:
            out.append(f"  ! advisor trigger {t.type}: {t.dedupe_key}")
    return "\n".join(out)


__all__ = ["fmt_pace", "render_week", "summarize_day"]
