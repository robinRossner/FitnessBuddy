"""Coach API for the iOS Coach tab (bearer token like the other APIs). Contract and examples:
docs/api/coach.md.

GET  /coach/proposals?status=pending|applied|rejected|all     proposals, newest first
GET  /coach/proposals/{id}                                    one proposal + the input sent
POST /coach/proposals/{id}/approve   {"change_ids": [..]|null}  apply (all valid ones if null)
POST /coach/proposals/{id}/reject    {"note": str|null}
POST /coach/ask                      {"scope","ref_id","question"}  202, answered in the background
GET  /coach/history                  config versions with author, reason, diff and outcome
GET  /coach/settings, PUT /coach/settings {"approval_mode"}   (+ provider, billing, model, ...)
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Annotated, Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from apps.fitness.advisor import claude, service
from apps.fitness.advisor.serialize import history_json, proposal_json
from apps.fitness.api.routes import get_db
from apps.fitness.jobs.advisor_jobs import run_consultation
from apps.fitness.settings import Settings, get_settings
from core.auth import require_token

router = APIRouter(prefix="/coach", dependencies=[Depends(require_token)])

Status = Literal["pending", "applied", "rejected", "all"]
# the app settings, once per request (overridable like any dependency)
AppSettings = Annotated[Settings, Depends(get_settings)]


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


def _settings_json(db: Session, settings: Settings) -> dict:
    s = service.coach_settings(db, settings)
    return {
        **claude.provider_status(settings),
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
    rows = service.list_proposals(db, status, limit)
    return {"items": [proposal_json(db, c) for c in rows]}


@router.get("/proposals/{proposal_id}")
def get_proposal(proposal_id: str, db: Session = Depends(get_db)) -> dict:
    with _errors():
        c = service.get_consultation(db, proposal_id)
    return proposal_json(db, c, detail=True)


@router.post("/proposals/{proposal_id}/approve")
def approve(
    proposal_id: str,
    settings: AppSettings,
    body: ApproveBody | None = None,
    db: Session = Depends(get_db),
) -> dict:
    with _errors():
        c = service.approve_proposal(
            db, proposal_id, (body.change_ids if body else None), settings=settings
        )
    return proposal_json(db, c)


@router.post("/proposals/{proposal_id}/reject")
def reject(proposal_id: str, body: RejectBody | None = None, db: Session = Depends(get_db)) -> dict:
    with _errors():
        c = service.reject_proposal(db, proposal_id, body.note if body else None)
    return proposal_json(db, c)


@router.post("/ask", status_code=202)
def ask(
    body: AskBody,
    background: BackgroundTasks,
    settings: AppSettings,
    db: Session = Depends(get_db),
) -> dict:
    with _errors():
        c = service.start_ask(
            db, scope=body.scope, ref_id=body.ref_id, question=body.question, settings=settings
        )
    background.add_task(run_consultation, c.id, settings)
    return proposal_json(db, c)


@router.get("/history")
def history(settings: AppSettings, db: Session = Depends(get_db)) -> dict:
    service.advisor_config(db, settings)  # makes sure the config is seeded
    return history_json(db)


@router.get("/settings")
def get_coach_settings(settings: AppSettings, db: Session = Depends(get_db)) -> dict:
    return _settings_json(db, settings)


@router.put("/settings")
def put_coach_settings(
    body: SettingsBody, settings: AppSettings, db: Session = Depends(get_db)
) -> dict:
    with _errors():
        service.set_approval_mode(db, body.approval_mode, settings)
    return _settings_json(db, settings)
