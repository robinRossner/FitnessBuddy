"""What the Claude advisor may change, one module per rule family:

    base.py ....... Invalid, Context, Spec / Checked, the generic number / choice / switch checks
    risk.py ....... risk classification (low | medium | high), decided here, never by Claude
    lifting.py .... rep ranges, exercise overrides and swaps, rest, increments, stalls, deloads
    running.py .... volume, long run, easy calibration, VDOT, threshold, intervals, phases
    allowlist.py .. SPECS (the allowlist in prompt order), validate_change / accept, build_context
    schedule.py ... schedule changes (move / skip / swap / shorten / replace)
    overrides.py .. session overrides never name a weight or a pace

`apps.fitness.advisor.validator` re-exports the entry points below.
"""

from __future__ import annotations

from apps.fitness.advisor.rules.allowlist import (
    SPECS,
    Verdict,
    accept,
    build_context,
    describe_allowlist,
    find_spec,
    label_for,
    validate_change,
)
from apps.fitness.advisor.rules.base import SLUG, Checked, Context, Invalid, Spec, to_number
from apps.fitness.advisor.rules.overrides import override_problem
from apps.fitness.advisor.rules.risk import HIGH, LOW, MEDIUM, RISKS
from apps.fitness.advisor.rules.schedule import (
    SCHEDULE_ACTIONS,
    ScheduleContext,
    ScheduleVerdict,
    accept_schedule,
    build_schedule_context,
    schedule_label,
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
