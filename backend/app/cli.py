"""Small admin CLI: `uv run python -m app.cli {bodyweight|stats} ...`."""

from __future__ import annotations

from datetime import date, datetime, time
from typing import Annotated
from zoneinfo import ZoneInfo

import typer
from sqlalchemy import select

from app.bodyweight import manual_id, upsert_measurement
from app.db.models import BodyMeasurement
from app.db.session import session_scope
from app.engine.config import load_engine_config
from app.settings import get_settings
from app.stats import rebuild_stats

cli = typer.Typer(add_completion=False, no_args_is_help=True, help="FitnessBuddy admin tasks")
bodyweight = typer.Typer(no_args_is_help=True, help="Bodyweight log")
stats = typer.Typer(no_args_is_help=True, help="Derived stats")
cli.add_typer(bodyweight, name="bodyweight")
cli.add_typer(stats, name="stats")


def _rebuild(db) -> int:
    s = get_settings()
    cfg = load_engine_config(s.engine_config_path)
    return rebuild_stats(
        db,
        bodyweight_kg=cfg.user.bodyweight_kg,
        max_reps=cfg.strength.e1rm.max_reps,
        min_reps=cfg.strength.e1rm.min_reps,
    )


@bodyweight.command("add")
def bodyweight_add(
    weight_kg: Annotated[float, typer.Argument(help="weight in kg")],
    day: Annotated[datetime | None, typer.Option("--date", formats=["%Y-%m-%d"])] = None,
    note: Annotated[str | None, typer.Option()] = None,
    rebuild: Annotated[bool, typer.Option("--rebuild/--no-rebuild")] = True,
) -> None:
    """Record a manual weigh-in. One entry per day: running it again the same day updates it."""
    s = get_settings()
    d: date = day.date() if day else datetime.now(ZoneInfo(s.user_tz)).date()
    when = datetime.combine(d, time(12, 0), tzinfo=ZoneInfo(s.user_tz))
    with session_scope() as db:
        old = db.get(BodyMeasurement, manual_id(d))
        if old is not None and old.deleted_at is not None:
            old.deleted_at = None  # an explicit CLI add revives a deleted entry
        row, created = upsert_measurement(
            db, id=manual_id(d), measured_at=when, weight_kg=weight_kg, tz=s.user_tz, note=note
        )
        n = _rebuild(db) if rebuild else None
    typer.echo(
        f"{'added' if created else 'updated'} {row.weight_kg:g} kg on {row.day}"
        + (f" (stats rebuilt: {n} rows)" if n is not None else "")
    )


@bodyweight.command("list")
def bodyweight_list() -> None:
    with session_scope() as db:
        rows = db.scalars(
            select(BodyMeasurement)
            .where(BodyMeasurement.deleted_at.is_(None))
            .order_by(BodyMeasurement.measured_at)
        )
        for r in rows:
            typer.echo(f"{r.day}  {r.weight_kg:g} kg  {r.source}  {r.id}  {r.note or ''}")


@stats.command("rebuild")
def stats_rebuild() -> None:
    """Recompute e1RM and per-session stats (e.g. after changing the bodyweight log)."""
    with session_scope() as db:
        typer.echo(f"exercise_session_stats rows: {_rebuild(db)}")


if __name__ == "__main__":
    cli()
