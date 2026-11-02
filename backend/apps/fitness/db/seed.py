"""Idempotent seeding: default gym + engine config version.

Run standalone: `uv run python -m apps.fitness.db.seed`
"""

from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from apps.fitness.db.models import ConfigVersion, Gym
from apps.fitness.engine.core.config import load_engine_config
from apps.fitness.engine.core.config_files import content_hash, load_raw
from apps.fitness.settings import FITNESS_DIR, get_settings

log = logging.getLogger(__name__)


def ensure_default_gym(db: Session, name: str, tz: str | None = None) -> Gym:
    gym = db.scalar(select(Gym).where(Gym.name == name))
    if gym is None:
        gym = Gym(name=name, timezone=tz, notes="Default gym; all Strong history is assigned here.")
        db.add(gym)
        db.flush()
    return gym


def ensure_config_version(db: Session, path: Path, *, activate: bool = True) -> ConfigVersion:
    """Insert the config file as an immutable version row; `activate` makes it the active one.

    If a row with the same version exists but the file content changed, the stored row is kept
    and a warning is logged: published versions are immutable, bump `version` instead.
    """
    raw = load_raw(path)
    cfg = load_engine_config(path)  # validates
    digest = content_hash(raw)
    row = db.scalar(select(ConfigVersion).where(ConfigVersion.version == cfg.version))
    if row is None:
        has_active = db.scalar(select(ConfigVersion).where(ConfigVersion.is_active)) is not None
        is_active = activate or not has_active
        if is_active:
            for other in db.scalars(select(ConfigVersion).where(ConfigVersion.is_active)):
                other.is_active = False
        try:
            src = str(path.resolve().relative_to(FITNESS_DIR))
        except ValueError:
            src = str(path)
        row = ConfigVersion(
            version=cfg.version,
            content=raw,
            content_hash=digest,
            source_path=src,
            is_active=is_active,
        )
        db.add(row)
        db.flush()
    elif row.content_hash != digest:
        log.warning(
            "config %s changed on disk but version was not bumped; DB keeps the original",
            cfg.version,
        )
    return row


def main() -> None:
    from apps.fitness.db.session import session_scope

    s = get_settings()
    with session_scope() as db:
        gym = ensure_default_gym(db, s.default_gym_name, s.user_tz)
        cv = ensure_config_version(db, s.engine_config_path)
        print(f"gym={gym.name!r} config={cv.version} active={cv.is_active}")


if __name__ == "__main__":
    main()
