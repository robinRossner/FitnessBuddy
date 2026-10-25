"""Per-exercise settings for the iOS app (bearer token like the other write APIs).

PUT /api/v1/exercises/{variant_id}/rest   body {"rest_s": 30..600 | null}

Sets the rest timer of the variant's exercise (all variants of one exercise share it; the setting
is `exercise_overrides.<slug>.rest_s` in the engine config). `null` resets it to the default
(`lifting.rest_default_s`, 120). It creates a config version authored by "user" and is idempotent:
the same value again creates no new version. Contract: docs/api/exercises.md.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import update
from sqlalchemy.orm import Session

from app.api.auth import require_token
from app.api.routes import get_db
from app.db.models import Exercise, ExerciseVariant, PlannedSession
from app.db.types import utcnow
from app.engine import config_service as cs
from app.engine import rest
from app.engine import service as engine
from app.engine.config import REST_MAX_S, REST_MIN_S, EngineConfig
from app.settings import get_settings

router = APIRouter(prefix="/api/v1", dependencies=[Depends(require_token)])


class RestBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # null = back to the default; a missing key is an error (422), not a reset
    rest_s: Annotated[int, Field(strict=True, ge=REST_MIN_S, le=REST_MAX_S)] | None


def clock(seconds: int) -> str:
    return f"{seconds // 60}:{seconds % 60:02d}"


@router.put("/exercises/{variant_id}/rest")
def put_rest(variant_id: int, body: RestBody, db: Session = Depends(get_db)) -> dict[str, Any]:
    variant = db.get(ExerciseVariant, variant_id)
    if variant is None or variant.deleted_at is not None:
        raise HTTPException(404, "variant not found")
    exercise = db.get(Exercise, variant.exercise_id)
    if exercise is None:  # pragma: no cover - FK guarantees it
        raise HTTPException(404, "exercise not found")
    settings = get_settings()
    cs.ensure_seeded(db, settings.engine_config_path)
    cfg, active = cs.load_active(db)
    path = f"exercise_overrides.{exercise.slug}.rest_s"
    name = variant.name.split(" @ ")[0]
    default = cfg.lifting_cfg.rest_default_s
    if body.rest_s is None:
        row = cs.create_version(
            db, {}, unset=(path,), author="user",
            reason=f"Rest time for {name} reset to the default ({clock(default)})",
        )  # fmt: skip
    else:
        row = cs.create_version(
            db, {path: body.rest_s}, author="user",
            reason=f"Rest time for {name} set to {clock(body.rest_s)}",
        )  # fmt: skip
    if row.id != active.id:
        # open lifting plans are re-sent with the new rest_s on the next pull
        db.execute(
            update(PlannedSession)
            .where(
                PlannedSession.kind.in_(("upper", "lower")),
                PlannedSession.deleted_at.is_(None),
                PlannedSession.day >= engine.user_today(settings),
            )
            .values(updated_at=utcnow())
        )
    db.commit()
    new_cfg = EngineConfig.model_validate(row.content)
    return {
        "variant_id": variant.id,
        "rest_s": rest.resolve_rest_s(new_cfg, exercise.slug),
        "is_default": rest.override_for(new_cfg, exercise.slug) is None,
        "config_version": row.version,
    }
