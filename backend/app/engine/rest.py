"""Rest timers: the config's default and the per-exercise override.

`lifting.rest_default_s` (120) applies to every exercise; `exercise_overrides.<slug>.rest_s`
replaces it for one exercise (the user sets it with `PUT /api/v1/exercises/{variant_id}/rest`, the
advisor may propose it). Plan items carry the resolved value as `rest_s`; it is resolved when a plan
is read, so a change applies to sessions that are already planned.
"""

from __future__ import annotations

import copy
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Exercise, ExerciseVariant
from app.engine.config import REST_MAX_S, REST_MIN_S, EngineConfig


def valid_rest(value: Any) -> bool:
    return (
        isinstance(value, int) and not isinstance(value, bool) and REST_MIN_S <= value <= REST_MAX_S
    )


def override_for(cfg: EngineConfig, slug: str | None) -> int | None:
    """The stored per-exercise rest time, or None. Garbage in the config is ignored."""
    if not slug:
        return None
    raw = (cfg.exercise_overrides.get(slug) or {}).get("rest_s")
    try:
        val = int(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None
    return val if val is not None and valid_rest(val) else None


def resolve_rest_s(cfg: EngineConfig, slug: str | None) -> int:
    ov = override_for(cfg, slug)
    return ov if ov is not None else cfg.lifting_cfg.rest_default_s


def overrides_map(cfg: EngineConfig) -> dict[str, int]:
    """slug -> rest_s for every exercise with an override (for the sync pull)."""
    out: dict[str, int] = {}
    for slug in cfg.exercise_overrides:
        ov = override_for(cfg, slug)
        if ov is not None:
            out[slug] = ov
    return out


def overrides_by_variant(db: Session, cfg: EngineConfig) -> dict[str, int]:
    """variant id (as a string, JSON keys) -> rest_s for every live variant of an exercise with an
    override: the iOS app keys its local overrides by variant id."""
    by_slug = overrides_map(cfg)
    if not by_slug:
        return {}
    rows = db.execute(
        select(ExerciseVariant.id, Exercise.slug)
        .join(Exercise, Exercise.id == ExerciseVariant.exercise_id)
        .where(Exercise.slug.in_(list(by_slug)), ExerciseVariant.deleted_at.is_(None))
    )
    return {str(vid): by_slug[slug] for vid, slug in rows}


def annotate_session(session: dict[str, Any] | None, cfg: EngineConfig) -> dict[str, Any] | None:
    """Copy of a plan item's `session` with `rest_s` on every lifting exercise (unchanged for
    other session types; the input is not modified)."""
    if not session or session.get("type") != "lifting":
        return session
    out = copy.copy(session)
    out["exercises"] = [
        {**ex, "rest_s": resolve_rest_s(cfg, ex.get("exercise"))}
        for ex in session.get("exercises") or []
    ]
    return out
