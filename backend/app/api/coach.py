"""Coach API for the iOS Coach tab (bearer token like the other APIs). Contract and examples:
docs/api/coach.md.

GET  /coach/proposals?status=pending|applied|rejected|all     proposals, newest first
GET  /coach/proposals/{id}                                    one proposal + the input sent
POST /coach/proposals/{id}/approve   {"change_ids": [..]|null}  apply (all valid ones if null)
POST /coach/proposals/{id}/reject    {"note": str|null}
POST /coach/ask                      {"scope","ref_id","question"}  202, answered in the background
GET  /coach/history                  config versions with author, reason, diff and outcome
GET  /coach/settings, PUT /coach/settings {"approval_mode"}
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Annotated, Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.advisor import service
from app.advisor.serialize import history_json, proposal_json
from app.api.auth import require_token
from app.api.routes import get_db
from app.db.models import AdvisorConsultation
from app.jobs.advisor_jobs import run_consultation

router = APIRouter(prefix="/api/v1/coach", dependencies=[Depends(require_token)])

Status = Literal["pending", "applied", "rejected", "all"]


class ApproveBody(BaseModel):
    change_ids: list[str | int] | None = None


class RejectBody(BaseModel):
    note: str | None = None


class AskBody(BaseModel):
    scope: Literal["lift", "run", "week", "general"]
    ref_id: str | None = None
    question: str


class SettingsBody(BaseModel):
    approval_mode: Literal["manual", "auto-low-risk", "auto"]


@contextmanager
def _errors() -> Iterator[None]:
    try:
        yield
    except service.AdvisorError as exc:
        raise HTTPException(exc.status_code, str(exc)) from None


def _settings_json(db: Session) -> dict:
    s = service.coach_settings(db)
    return {
        "approval_mode": s.approval_mode,
        "api_key_configured": s.api_key_configured,
        "dry_run": s.dry_run,
        "calls_this_week": s.calls_this_week,
        "max_calls_per_week": s.max_calls_per_week,
    }


@router.get("/proposals")
def list_proposals(
    status: Status = "all",
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    db: Session = Depends(get_db),
) -> dict:
    q = select(AdvisorConsultation).order_by(AdvisorConsultation.id.desc())
    if status == "pending":
        rows = service.pending_proposals(db)[:limit]
    else:
        if status == "applied":
            q = q.where(AdvisorConsultation.status.in_(("applied", "partially_applied")))
        elif status == "rejected":
            q = q.where(AdvisorConsultation.status == "rejected")
        rows = list(db.scalars(q.limit(limit)))
    return {"items": [proposal_json(db, c) for c in rows]}


@router.get("/proposals/{proposal_id}")
def get_proposal(proposal_id: str, db: Session = Depends(get_db)) -> dict:
    with _errors():
        c = service.get_consultation(db, proposal_id)
    return proposal_json(db, c, detail=True)


@router.post("/proposals/{proposal_id}/approve")
def approve(
    proposal_id: str, body: ApproveBody | None = None, db: Session = Depends(get_db)
) -> dict:
    with _errors():
        c = service.get_consultation(db, proposal_id)
        version = service.approve(db, c, (body.change_ids if body else None))
    if version is not None:
        service.rerun_engine_after_change()
    db.refresh(c)
    return proposal_json(db, c)


@router.post("/proposals/{proposal_id}/reject")
def reject(proposal_id: str, body: RejectBody | None = None, db: Session = Depends(get_db)) -> dict:
    with _errors():
        c = service.get_consultation(db, proposal_id)
        service.reject(db, c, body.note if body else None)
    return proposal_json(db, c)


@router.post("/ask", status_code=202)
def ask(body: AskBody, background: BackgroundTasks, db: Session = Depends(get_db)) -> dict:
    with _errors():
        c = service.start_ask(db, scope=body.scope, ref_id=body.ref_id, question=body.question)
    background.add_task(run_consultation, c.id)
    return proposal_json(db, c)


@router.get("/history")
def history(db: Session = Depends(get_db)) -> dict:
    service.advisor_config(db)  # makes sure the config is seeded
    return history_json(db)


@router.get("/settings")
def get_coach_settings(db: Session = Depends(get_db)) -> dict:
    return _settings_json(db)


@router.put("/settings")
def put_coach_settings(body: SettingsBody, db: Session = Depends(get_db)) -> dict:
    with _errors():
        service.set_approval_mode(db, body.approval_mode)
    return _settings_json(db)
