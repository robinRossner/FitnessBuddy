"""Advisor lifecycle facade: the public functions of the advisor, re-exported from the modules that
implement them. Callers (API, jobs, CLI, outcomes) import from here.

    _common.py ........ trigger / proposal types, limits, errors, advisor config, status rule
    budget.py ......... weekly call budget, cost of a call
    consultations.py .. create / run / store + validate the answer, recover + retry
    approval.py ....... approve / reject / apply changes, approval mode, Coach settings
    triggers.py ....... engine triggers -> trigger batch, the Sunday weekly review
    ask.py ............ "Ask coach": target check, daily limit, consultation

`rerun_engine_after_change` is looked up on this module at call time (`_common.rerun_hook`), so
patching it here reaches every caller.
"""

from __future__ import annotations

from apps.fitness.advisor._common import (
    ASK,
    COUNTED,
    COVER_DAYS,
    DATA_ANOMALY,
    LIFT_STALL,
    MAX_CHANGES,
    MAX_SCHEDULE_CHANGES,
    MISSED_SESSION,
    PRIORITY,
    RULE_CONFLICT,
    RUNNABLE,
    RUNNING_STAGNATION,
    STALE_RUNNING_MIN,
    TRIGGER_MAP,
    USAGE_LIMIT_MESSAGE,
    WEEKLY_REVIEW,
    AdvisorError,
    AskLimit,
    Conflict,
    NotFound,
    Unprocessable,
    advisor_config,
    program_raw,
    rerun_engine_after_change,
    settle_status,
    user_today,
)
from apps.fitness.advisor.approval import (
    CoachSettings,
    apply_changes,
    apply_schedule_changes,
    approve,
    change_ref,
    coach_settings,
    reject,
    set_approval_mode,
    summary_value,
)
from apps.fitness.advisor.ask import ask_trigger_info, asks_today, check_ask_budget, start_ask
from apps.fitness.advisor.budget import budget_allows, calls_this_week, cost_usd
from apps.fitness.advisor.consultations import (
    create_consultation,
    execute,
    get_consultation,
    pending_proposals,
    pending_summary,
    recover_stale,
    retry_awaiting_key,
    retry_usage_limited,
    store_output,
)
from apps.fitness.advisor.triggers import (
    _describe,  # noqa: F401  (tests/engine/test_progression_advisor.py)
    open_triggers,
    run_trigger_batch,
    run_weekly_review,
    trigger_info_from_rows,
    weekly_trigger_info,
)
from apps.fitness.engine import service as engine_service  # noqa: F401  (cli.py: advisor input)

__all__ = [
    "ASK",
    "COUNTED",
    "COVER_DAYS",
    "DATA_ANOMALY",
    "LIFT_STALL",
    "MAX_CHANGES",
    "MAX_SCHEDULE_CHANGES",
    "MISSED_SESSION",
    "PRIORITY",
    "RULE_CONFLICT",
    "RUNNABLE",
    "RUNNING_STAGNATION",
    "STALE_RUNNING_MIN",
    "TRIGGER_MAP",
    "USAGE_LIMIT_MESSAGE",
    "WEEKLY_REVIEW",
    "AdvisorError",
    "AskLimit",
    "CoachSettings",
    "Conflict",
    "NotFound",
    "Unprocessable",
    "advisor_config",
    "apply_changes",
    "apply_schedule_changes",
    "approve",
    "ask_trigger_info",
    "asks_today",
    "budget_allows",
    "calls_this_week",
    "change_ref",
    "check_ask_budget",
    "coach_settings",
    "cost_usd",
    "create_consultation",
    "execute",
    "get_consultation",
    "open_triggers",
    "pending_proposals",
    "pending_summary",
    "program_raw",
    "recover_stale",
    "reject",
    "rerun_engine_after_change",
    "retry_awaiting_key",
    "retry_usage_limited",
    "run_trigger_batch",
    "run_weekly_review",
    "set_approval_mode",
    "settle_status",
    "start_ask",
    "store_output",
    "summary_value",
    "trigger_info_from_rows",
    "user_today",
    "weekly_trigger_info",
]
