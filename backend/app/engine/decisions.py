"""Writing the audit trail: decisions_log rows and advisor triggers (idempotent)."""

from __future__ import annotations

import json
from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import AdvisorTrigger, DecisionLog

ENGINE_VERSION = "engine-1"


def _canon(value: Any) -> str:
    return json.dumps(value, sort_keys=True, default=str)


def log_decision(
    db: Session,
    *,
    kind: str,
    rule: str,
    inputs: dict[str, Any],
    output: dict[str, Any],
    rationale: str,
    subject_type: str | None = None,
    subject_id: str | int | None = None,
    config_version_id: int | None = None,
    planned_session_id: str | None = None,
) -> DecisionLog | None:
    """Insert a decision unless the latest one for the same subject is identical.

    `rule` is stored inside `output["rule"]`. Returns the new row, or None when it was a repeat
    (regenerating a plan never piles up duplicate rows, but a changed outcome is a new row).
    """
    out = {"rule": rule, **output}
    sid = str(subject_id) if subject_id is not None else None
    last = db.scalars(
        select(DecisionLog)
        .where(
            DecisionLog.kind == kind,
            DecisionLog.subject_type == subject_type,
            DecisionLog.subject_id == sid,
            DecisionLog.planned_session_id == planned_session_id,
        )
        .order_by(DecisionLog.id.desc())
        .limit(60)
    ).all()
    # latest decision of the same rule for this subject (a subject can carry several rules)
    last = next((r for r in last if (r.output or {}).get("rule") == rule), None)
    if (
        last is not None
        and _canon(last.inputs) == _canon(inputs)
        and _canon(last.output) == _canon(out)
    ):
        return None
    row = DecisionLog(
        kind=kind,
        subject_type=subject_type,
        subject_id=sid,
        config_version_id=config_version_id,
        engine_version=ENGINE_VERSION,
        planned_session_id=planned_session_id,
        inputs=json.loads(_canon(inputs)),
        output=json.loads(_canon(out)),
        rationale=rationale,
    )
    db.add(row)
    db.flush()
    return row


def add_trigger(
    db: Session,
    *,
    type: str,
    dedupe_key: str,
    payload: dict[str, Any],
    subject_type: str | None = None,
    subject_id: str | int | None = None,
    day: date | None = None,
    decision_id: int | None = None,
) -> AdvisorTrigger | None:
    """Record an advisor trigger once per `dedupe_key`."""
    if db.scalar(select(AdvisorTrigger.id).where(AdvisorTrigger.dedupe_key == dedupe_key)):
        return None
    row = AdvisorTrigger(
        type=type,
        dedupe_key=dedupe_key,
        subject_type=subject_type,
        subject_id=str(subject_id) if subject_id is not None else None,
        day=day,
        payload=json.loads(_canon(payload)),
        decision_id=decision_id,
    )
    db.add(row)
    db.flush()
    return row
