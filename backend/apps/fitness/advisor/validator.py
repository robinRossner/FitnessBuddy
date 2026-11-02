"""Allowlist + hard limits for what the Claude advisor may change, and the risk classification.

Claude can only propose *parameters* (a dotted path into the engine config and a new value); it
never sets weights or paces, the engine recomputes those from the parameters. Everything that is
not in `SPECS` is rejected. Each change is validated on its own (`validate_change`) against the
config content as it would be after the earlier valid changes of the same proposal, and gets:

- `valid` / `error`: the type, range, step and cross-field checks below,
- `risk`: low | medium | high, decided HERE (never by Claude). `low` is deliberately narrow:
  rep-range shifts of at most 1 rep per end and interval rep counts of +-1. The
  `auto-low-risk` approval mode only applies those.

Schedule changes (move / skip / swap / shorten / replace of planned sessions) have their own
validation below (`validate_schedule_change`).

Hard limits (see `describe_allowlist()`, which also feeds the system prompt):
rep ranges 3-20 (low < high), stall exposures 2-6 (2-8 twice a week), stall reset 5-15 % (first
ladder step 2.5-10 %), deload red days 2-7, interval reps 3-10, rep distance 200-2000 m, jog
recovery 100-800 m, weekly volume cap 0-15 % (+0-4 km), weekly volume target 10-60 km, long run
cap <= 25 km, VDOT steps <= 1.0 (<= 2.0 per 4 weeks), increments only from the options of their
equipment, exercise swaps only to active variants of the same movement pattern or body region
at the current gym (or generic). Every change also has a maximum step ("max load jump" /
"volume ramp"): a single proposal cannot move a limit across its whole range.

The rules live in `advisor/rules/` (one module per rule family, see its `__init__`); this module
re-exports their entry points.
"""

from __future__ import annotations

from apps.fitness.advisor.rules import (
    HIGH,
    LOW,
    MEDIUM,
    RISKS,
    SCHEDULE_ACTIONS,
    SLUG,
    SPECS,
    Checked,
    Context,
    Invalid,
    ScheduleContext,
    ScheduleVerdict,
    Spec,
    Verdict,
    accept,
    accept_schedule,
    build_context,
    build_schedule_context,
    describe_allowlist,
    find_spec,
    label_for,
    override_problem,
    schedule_label,
    to_number,
    validate_change,
    validate_schedule_change,
)

__all__ = [
    "HIGH",
    "LOW",
    "MEDIUM",
    "RISKS",
    "SCHEDULE_ACTIONS",
    "SLUG",
    "SPECS",
    "Checked",
    "Context",
    "Invalid",
    "ScheduleContext",
    "ScheduleVerdict",
    "Spec",
    "Verdict",
    "accept",
    "accept_schedule",
    "build_context",
    "build_schedule_context",
    "describe_allowlist",
    "find_spec",
    "label_for",
    "override_problem",
    "schedule_label",
    "to_number",
    "validate_change",
    "validate_schedule_change",
]
