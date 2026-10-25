"""JSON of the coach API (contract: docs/api/coach.md). snake_case, ISO UTC `Z` timestamps."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import AdvisorChange, AdvisorConsultation, ConfigVersion, Outcome
from app.db.types import iso_z


def change_json(x: AdvisorChange) -> dict[str, Any]:
    return {
        "id": str(x.id),
        "param": x.param,
        "label": x.label,
        "from": x.old_value,
        "to": x.new_value,
        "reason": x.reason,
        "risk": x.risk,
        "valid": bool(x.valid),
        "validation_error": x.validation_error,
        "status": x.status,
    }


def proposal_json(db: Session, c: AdvisorConsultation, *, detail: bool = False) -> dict[str, Any]:
    info = c.trigger_info or {}
    ref = info.get("ref") or {}
    resp = c.response or {}
    version = (
        db.get(ConfigVersion, c.applied_config_version_id) if c.applied_config_version_id else None
    )
    out: dict[str, Any] = {
        "id": str(c.id),
        "created_at": iso_z(c.created_at),
        "status": c.status,
        "trigger": {
            "type": info.get("type") or c.trigger,
            "title": info.get("title") or "",
            "detail": info.get("detail") or "",
            "ref": {"kind": ref.get("kind"), "id": ref.get("id")},
        },
        "question": c.question,
        "diagnosis": resp.get("diagnosis"),
        "notes_for_user": resp.get("notes_for_user"),
        "confidence": resp.get("confidence"),
        "changes": [change_json(x) for x in c.changes],
        "session_overrides": resp.get("session_overrides") or [],
        "applied_config_version": version.version if version else None,
        "error": c.error,
        "cost_usd": round(c.cost_usd, 4) if c.cost_usd is not None else None,
    }
    if detail:
        out["input_summary"] = c.request or {}
    return out


def outcome_json(o: Outcome | None) -> dict[str, Any] | None:
    if o is None:
        return None
    m = o.metrics or {}
    return {
        "verdict": o.verdict,
        "notes": o.notes,
        "metric_before": m.get("before") or {},
        "metric_after": m.get("after") or {},
    }


def history_json(db: Session) -> dict[str, Any]:
    from app.engine import config_service as cs

    outcomes = {
        o.config_version_id: o
        for o in db.scalars(select(Outcome).where(Outcome.config_version_id.is_not(None)))
    }
    versions = []
    for v in cs.history(db):
        versions.append(
            {
                "version": v.version,
                "created_at": iso_z(v.created_at),
                "author": v.author,
                "reason": v.reason,
                "diff": v.diff or {},
                "consultation_id": (
                    str((v.meta or {})["consultation_id"])
                    if (v.meta or {}).get("consultation_id") is not None
                    else None
                ),
                "outcome": outcome_json(outcomes.get(v.id)),
                "active": bool(v.is_active),
            }
        )
    return {"versions": versions}
