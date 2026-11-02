"""The ad hoc "Ask coach" flow: validate what the question is about, enforce the daily limit and
create the consultation (run in the background by `jobs.advisor_jobs.run_consultation`).
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from apps.fitness.advisor._common import (
    ASK,
    AskLimit,
    Unprocessable,
    _local_midnight_utc,
    advisor_config,
    resolve_settings,
    user_today,
)
from apps.fitness.advisor.consultations import create_consultation
from apps.fitness.db.models import (
    AdvisorConsultation,
    ExerciseVariant,
    Run,
)
from apps.fitness.engine.store import service as engine_service
from apps.fitness.settings import Settings

log = logging.getLogger(__name__)


def asks_today(db: Session, today: date, settings: Settings | None = None) -> int:
    s = resolve_settings(settings)
    since = _local_midnight_utc(today, s)
    return (
        db.scalar(
            select(func.count())
            .select_from(AdvisorConsultation)
            .where(AdvisorConsultation.kind == "ask", AdvisorConsultation.created_at >= since)
        )
        or 0
    )


def check_ask_budget(db: Session, today: date, settings: Settings | None = None) -> None:
    s = resolve_settings(settings)
    limit = advisor_config(db, s).max_ask_per_day
    if asks_today(db, today, s) >= limit:
        raise AskLimit(f"Daily limit of {limit} coach questions reached. Try again tomorrow.")


def ask_trigger_info(db: Session, scope: str, ref_id: str | None, question: str) -> dict[str, Any]:
    """Validate the target the user asks about and describe it. Raises Unprocessable."""
    if scope == "general":
        return {
            "type": ASK,
            "title": "Question to the coach",
            "detail": question[:200],
            "ref": {"kind": None, "id": None},
        }
    if not ref_id:
        raise Unprocessable(f"ref_id is required for scope '{scope}'")
    if scope == "lift":
        try:
            variant = db.get(ExerciseVariant, int(ref_id))
        except ValueError:
            variant = None
        if variant is None:
            raise Unprocessable(f"unknown lift (variant id) '{ref_id}'")
        title = f"Question about {variant.name}"
        ref_id = str(variant.id)
    elif scope == "run":
        run = db.get(Run, ref_id)
        if run is None:
            raise Unprocessable(f"unknown run '{ref_id}'")
        title = f"Question about the run on {(run.started_at_local or run.started_at_utc).date()}"
    elif scope == "week":
        try:
            ws = engine_service.monday(date.fromisoformat(ref_id))
        except ValueError:
            raise Unprocessable("ref_id must be a date (YYYY-MM-DD) for scope 'week'") from None
        ref_id = ws.isoformat()
        title = f"Question about the week of {ref_id}"
    else:
        raise Unprocessable("scope must be one of lift, run, week, general")
    return {
        "type": ASK,
        "title": title,
        "detail": question[:200],
        "ref": {"kind": scope, "id": ref_id},
    }


def start_ask(
    db: Session,
    *,
    scope: str,
    ref_id: str | None,
    question: str,
    settings: Settings | None = None,
) -> AdvisorConsultation:
    """Create the consultation of an "Ask coach" tap (checks the daily limit). Does not run it."""
    s = resolve_settings(settings)
    question = (question or "").strip()
    if not question:
        raise Unprocessable("question must not be empty")
    if len(question) > 2000:
        raise Unprocessable("question is too long (max 2000 characters)")
    info = ask_trigger_info(db, scope, ref_id, question)
    check_ask_budget(db, user_today(s), s)
    status = "queued" if (s.advisor_key or s.advisor_dry_run) else "awaiting_key"
    c = create_consultation(
        db, kind="ask", trigger_info=info, question=question, status=status, settings=s
    )
    db.commit()
    return c
