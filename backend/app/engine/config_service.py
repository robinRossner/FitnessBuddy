"""Versioned engine config in the database.

The YAML file (config/engine.vN.yaml) only *seeds* `config_versions`; afterwards the database is
the source of truth. Versions are immutable. Changes (by the engine, the advisor or the user)
create a new row that points at its parent, with a diff, a reason and an author, and become the
active version; a rollback is a new version whose content equals an older one, so every logged
decision keeps referencing exactly the config it was made with.

Auto-update from Garmin: `apply_garmin_hr` compares the newest Garmin max HR / LTHR in
`athlete_metrics` with the active config and, if they differ (and are newer than what a previous
auto-update or a rollback already handled), creates a version authored by `engine`.
"""

from __future__ import annotations

import copy
from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import AthleteMetric, ConfigVersion
from app.engine.config import EngineConfig, content_hash
from app.engine.decisions import log_decision

AUTHORS = ("seed", "engine", "advisor", "user")
GARMIN_METRIC_DAY = "garmin_metrics_day"


class ConfigError(ValueError):
    pass


def get_active(db: Session) -> ConfigVersion:
    row = db.scalar(select(ConfigVersion).where(ConfigVersion.is_active).limit(1))
    if row is None:
        raise ConfigError("no active config version (run `alembic upgrade head` and the seed)")
    return row


def load_active(db: Session) -> tuple[EngineConfig, ConfigVersion]:
    row = get_active(db)
    return EngineConfig.model_validate(row.content), row


def get_version(db: Session, ref: str | int) -> ConfigVersion:
    if isinstance(ref, int) or str(ref).isdigit():
        row = db.get(ConfigVersion, int(ref))
    else:
        row = db.scalar(select(ConfigVersion).where(ConfigVersion.version == str(ref)))
    if row is None:
        raise ConfigError(f"unknown config version {ref!r}")
    return row


def history(db: Session) -> list[ConfigVersion]:
    return list(db.scalars(select(ConfigVersion).order_by(ConfigVersion.id.desc())))


def _flatten(d: Any, prefix: str = "") -> dict[str, Any]:
    if isinstance(d, dict) and d:
        out: dict[str, Any] = {}
        for k, v in d.items():
            out.update(_flatten(v, f"{prefix}.{k}" if prefix else str(k)))
        return out
    return {prefix: d}


def compute_diff(old: dict[str, Any], new: dict[str, Any]) -> dict[str, dict[str, Any]]:
    a, b = _flatten(old), _flatten(new)
    return {
        k: {"old": a.get(k), "new": b.get(k)}
        for k in sorted(set(a) | set(b))
        if a.get(k) != b.get(k) and k != "version"
    }


def set_path(content: dict[str, Any], dotted: str, value: Any) -> None:
    node = content
    parts = dotted.split(".")
    for part in parts[:-1]:
        nxt = node.get(part)
        if not isinstance(nxt, dict):
            nxt = node[part] = {}
        node = nxt
    node[parts[-1]] = value


def unset_path(content: dict[str, Any], dotted: str) -> None:
    """Remove a key; parents that become empty are removed too (no stray `{slug: {}}`)."""
    parts = dotted.split(".")
    chain: list[dict[str, Any]] = [content]
    for part in parts[:-1]:
        nxt = chain[-1].get(part)
        if not isinstance(nxt, dict):
            return
        chain.append(nxt)
    chain[-1].pop(parts[-1], None)
    for node, key in zip(reversed(chain[:-1]), reversed(parts[:-1]), strict=True):
        if node.get(key) == {}:
            del node[key]
        else:
            break


def _next_label(db: Session, parent: ConfigVersion) -> str:
    family = parent.version.split(".")[0]
    taken = set(
        db.scalars(select(ConfigVersion.version).where(ConfigVersion.version.like(f"{family}.%")))
    )
    n = 1
    while f"{family}.{n}" in taken:
        n += 1
    return f"{family}.{n}"


def _insert_version(
    db: Session,
    parent: ConfigVersion,
    content: dict[str, Any],
    *,
    reason: str,
    author: str,
    meta: dict[str, Any],
) -> ConfigVersion:
    label = _next_label(db, parent)
    content = copy.deepcopy(content)
    content["version"] = label
    EngineConfig.model_validate(content)  # never store a config the engine cannot load
    for other in db.scalars(select(ConfigVersion).where(ConfigVersion.is_active)):
        other.is_active = False
    row = ConfigVersion(
        version=label,
        content=content,
        content_hash=content_hash(content),
        source_path=None,
        is_active=True,
        parent_id=parent.id,
        author=author,
        reason=reason,
        diff=compute_diff(parent.content, content),
        meta=meta,
    )
    db.add(row)
    db.flush()
    return row


def create_version(
    db: Session,
    changes: dict[str, Any],
    *,
    reason: str,
    author: str = "engine",
    meta: dict[str, Any] | None = None,
    consultation_id: int | None = None,
    unset: tuple[str, ...] = (),
) -> ConfigVersion:
    """New active version = active content + `changes` (dotted paths, e.g.
    {"running.heart_rate.lthr": 178}), minus the `unset` paths. Returns the active version
    unchanged if nothing differs.
    `consultation_id` (advisor versions) is kept in `meta["consultation_id"]`."""
    if author not in AUTHORS:
        raise ConfigError(f"author must be one of {AUTHORS}")
    parent = get_active(db)
    content = copy.deepcopy(parent.content)
    for path, value in changes.items():
        set_path(content, path, value)
    for path in unset:
        unset_path(content, path)
    if not compute_diff(parent.content, content):
        return parent
    extra = {"consultation_id": consultation_id} if consultation_id is not None else {}
    new_meta = {**(parent.meta or {}), **(meta or {}), **extra}
    if consultation_id is None:
        new_meta.pop("consultation_id", None)  # never inherit the parent's link
    return _insert_version(db, parent, content, reason=reason, author=author, meta=new_meta)


def latest_garmin_metrics(db: Session) -> dict[str, AthleteMetric]:
    out: dict[str, AthleteMetric] = {}
    for metric in ("max_hr", "lthr"):
        row = db.scalars(
            select(AthleteMetric)
            .where(AthleteMetric.metric == metric, AthleteMetric.source == "garmin")
            .order_by(AthleteMetric.day.desc())
            .limit(1)
        ).first()
        if row is not None:
            out[metric] = row
    return out


def rollback(
    db: Session, ref: str | int, *, author: str = "user", reason: str | None = None
) -> ConfigVersion:
    """Make the content of an older version active again (as a new version, history is kept)."""
    target = get_version(db, ref)
    current = get_active(db)
    if target.id == current.id:
        return current
    if not compute_diff(current.content, target.content):
        return current
    # Garmin values up to now count as handled, so the rollback is not undone by the next sync.
    meta = dict(current.meta or {})
    meta.pop("consultation_id", None)  # a rollback is not the advisor's doing
    metrics = latest_garmin_metrics(db)
    if metrics:
        meta[GARMIN_METRIC_DAY] = max(m.day for m in metrics.values()).isoformat()
    row = _insert_version(
        db,
        current,
        target.content,
        reason=reason or f"rollback to {target.version}",
        author=author,
        meta=meta,
    )
    row.meta = {**meta, "rollback_of": target.version}
    db.flush()
    return row


def apply_garmin_hr(db: Session) -> ConfigVersion | None:
    """Auto-update max HR / LTHR from Garmin. Returns the new version, or None if unchanged."""
    active = get_active(db)
    cfg = EngineConfig.model_validate(active.content)
    hr = cfg.running_cfg.heart_rate
    if hr.source != "garmin":
        return None
    handled: date | None = None
    raw_day = (active.meta or {}).get(GARMIN_METRIC_DAY)
    if raw_day:
        handled = date.fromisoformat(raw_day)
    metrics = latest_garmin_metrics(db)
    changes: dict[str, Any] = {}
    parts: list[str] = []
    seen_day: date | None = None
    for metric, label, current in (("lthr", "LTHR", hr.lthr), ("max_hr", "max HR", hr.max_hr)):
        m = metrics.get(metric)
        if m is None or (handled is not None and m.day <= handled):
            continue
        seen_day = max(seen_day, m.day) if seen_day else m.day
        if current is None or abs(m.value - current) > 1e-9:
            changes[f"running.heart_rate.{metric}"] = (
                int(m.value) if m.value == int(m.value) else m.value
            )
            old = "unset" if current is None else f"{current:g}"
            parts.append(f"Garmin reported {label} {old}→{m.value:g} on {m.day}")
    if not changes:
        return None
    reason = "; ".join(parts)
    new = create_version(
        db,
        changes,
        reason=reason,
        author="engine",
        meta={GARMIN_METRIC_DAY: seen_day.isoformat() if seen_day else None},
    )
    if new.id == active.id:
        return None
    log_decision(
        db,
        kind="config_update",
        rule="garmin_hr_autoupdate",
        subject_type="config",
        subject_id=new.version,
        config_version_id=new.id,
        inputs={
            "from_version": active.version,
            "garmin": {k: [m.day.isoformat(), m.value] for k, m in metrics.items()},
        },
        output={"new_version": new.version, "diff": new.diff},
        rationale=reason + "; zones and the easy-run ceiling are recomputed from the new values",
    )
    return new


def ensure_seeded(db: Session, path) -> ConfigVersion:
    """Seed the YAML file as a version if it is not in the DB yet; return the active version."""
    from app.db.seed import ensure_config_version

    ensure_config_version(db, path, activate=True)  # activates only a version that is new
    return get_active(db)
