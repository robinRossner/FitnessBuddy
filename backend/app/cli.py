"""Small admin CLI: `uv run python -m app.cli {bodyweight|stats|garmin} ...`."""

from __future__ import annotations

import logging
from datetime import date, datetime, time
from typing import Annotated
from zoneinfo import ZoneInfo

import typer
from sqlalchemy import select

from app.bodyweight import manual_id, upsert_measurement
from app.db.models import BodyMeasurement
from app.db.session import session_scope
from app.settings import get_settings
from app.stats import rebuild_with_config

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
cli = typer.Typer(add_completion=False, no_args_is_help=True, help="FitnessBuddy admin tasks")
bodyweight = typer.Typer(no_args_is_help=True, help="Bodyweight log")
stats = typer.Typer(no_args_is_help=True, help="Derived stats")
cli.add_typer(bodyweight, name="bodyweight")
garmin = typer.Typer(no_args_is_help=True, help="Garmin sync (read-only towards Garmin)")
cli.add_typer(stats, name="stats")
cli.add_typer(garmin, name="garmin")


def _rebuild(db) -> int:
    return rebuild_with_config(db)


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


def _kinds(value: str | None) -> tuple[str, ...]:
    from app.sources.garmin.base import ALL_KINDS

    if not value:
        return ALL_KINDS
    kinds = tuple(k.strip() for k in value.replace(" ", ",").split(",") if k.strip())
    bad = [k for k in kinds if k not in ALL_KINDS]
    if bad:
        raise typer.BadParameter(f"unknown kinds {bad}; choose from {', '.join(ALL_KINDS)}")
    return kinds


def _fail(exc: Exception) -> typer.Exit:
    typer.echo(f"ERROR: {type(exc).__name__}: {exc}", err=True)
    return typer.Exit(1)


@garmin.command("check")
def garmin_check(
    source: Annotated[str | None, typer.Option(help="personal_api | garminconnect | auto")] = None,
) -> None:
    """Exchange the token, hit one cheap endpoint per data kind, show what is reachable."""
    from sqlalchemy import func

    from app.db.models import DailyHealth, Run, SyncState
    from app.sources.garmin.errors import GarminError
    from app.sources.garmin.factory import build_source
    from app.sources.garmin.personal_api import PersonalApiSource

    s = get_settings()
    try:
        src = build_source(s, source)
        typer.echo(f"source: {src.name}")
        primary = getattr(src, "primary", src)
        if isinstance(primary, PersonalApiSource):
            primary.client.token()
            exp = primary.client.token_expires_at()
            typer.echo(f"token exchange: ok (bearer valid until {exp:%Y-%m-%d %H:%M} UTC)")
            typer.echo(
                "personal API key expiry: not exposed by the API "
                "(Garmin e-mails a reminder ~1 week before; see Garmin Connect settings)"
            )
            today = datetime.now(ZoneInfo(s.user_tz)).date()
            typer.echo("reachable data kinds (small probe request each):")
            for kind, info in primary.probe(today).items():
                state = "ok" if info["ok"] else f"FAILED ({info.get('error')})"
                typer.echo(f"  {kind:14} {state}, {info['rows']} row(s)")
            first = primary.client.get(
                "/activitylist-service/activities/search/activities",
                {"start": 0, "limit": 1, "sortOrder": "asc"},
            )
            if first:
                typer.echo(f"  earliest activity on Garmin: {first[0]['startTimeLocal'][:10]}")
        else:
            typer.echo("fallback source selected: no probe (would require a login)")
    except GarminError as exc:
        raise _fail(exc) from None
    with session_scope() as db:
        typer.echo("local coverage:")
        n_runs, first_run, last_run = db.execute(
            select(
                func.count(Run.id), func.min(Run.started_at_local), func.max(Run.started_at_local)
            ).where(Run.deleted_at.is_(None))
        ).one()
        typer.echo(f"  activities     {n_runs} ({first_run} .. {last_run})")
        n_days, first_day, last_day = db.execute(
            select(func.count(DailyHealth.id), func.min(DailyHealth.day), func.max(DailyHealth.day))
        ).one()
        typer.echo(f"  daily_health   {n_days} ({first_day} .. {last_day})")
        for st in db.scalars(select(SyncState).order_by(SyncState.kind)):
            err = f"  last error: {st.last_error}" if st.last_error else ""
            typer.echo(f"  cursor {st.kind:14} {st.cursor_date}{err}")


@garmin.command("backfill")
def garmin_backfill(
    since: Annotated[
        datetime | None, typer.Option(formats=["%Y-%m-%d"], help="default 2023-01-01")
    ] = None,
    kinds: Annotated[str | None, typer.Option(help="comma list; default all")] = None,
    source: Annotated[str | None, typer.Option(help="personal_api | garminconnect | auto")] = None,
    restart: Annotated[bool, typer.Option(help="ignore stored cursors, start at --since")] = False,
    max_waits: Annotated[
        int, typer.Option(help="rate-limit waits (1, 3, 10 min) before giving up")
    ] = 3,
) -> None:
    """Fetch history in resumable chunks (re-run to continue after an interruption)."""
    from app.db.session import get_session_factory
    from app.garmin_sync import DEFAULT_RATE_LIMIT_WAITS, GarminSync
    from app.jobs.garmin_jobs import after_sync
    from app.sources.garmin.errors import GarminError
    from app.sources.garmin.factory import build_source

    s = get_settings()
    start = since.date() if since else s.garmin_since
    try:
        sync = GarminSync(
            get_session_factory(),
            build_source(s, source),
            s,
            progress=typer.echo,
            rate_limit_waits=DEFAULT_RATE_LIMIT_WAITS[:max_waits],
        )
        report = sync.backfill(start, _kinds(kinds), restart=restart)
    except GarminError as exc:
        raise _fail(exc) from None
    after_sync(report)
    _echo_report(report)


@garmin.command("sync")
def garmin_sync(
    kinds: Annotated[str | None, typer.Option(help="comma list; default all")] = None,
    source: Annotated[str | None, typer.Option(help="personal_api | garminconnect | auto")] = None,
) -> None:
    """Incremental sync: from the stored cursor minus a small overlap up to today."""
    from app.db.session import get_session_factory
    from app.garmin_sync import GarminSync
    from app.jobs.garmin_jobs import after_sync
    from app.sources.garmin.errors import GarminError
    from app.sources.garmin.factory import build_source

    s = get_settings()
    try:
        sync = GarminSync(get_session_factory(), build_source(s, source), s, progress=typer.echo)
        report = sync.sync(_kinds(kinds))
    except GarminError as exc:
        raise _fail(exc) from None
    after_sync(report)
    _echo_report(report)


@garmin.command("reparse")
def garmin_reparse() -> None:
    """Re-run the parsers over stored raw JSON (no network)."""
    from app.db.session import get_session_factory
    from app.garmin_sync import GarminSync

    s = get_settings()
    sync = GarminSync(get_session_factory(), None, s)
    typer.echo(f"reparsed: {sync.reparse()}")


def _echo_report(report) -> None:
    typer.echo("done:")
    for r in report.results:
        note = f" ({r.skipped})" if r.skipped else ""
        typer.echo(f"  {r.kind:14} {r.rows} rows in {r.chunks} chunk(s){note}")
    typer.echo(
        f"  activity details fetched: {report.details_fetched}, failed: {report.details_failed}"
    )


if __name__ == "__main__":
    cli()
