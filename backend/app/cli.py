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
advisor = typer.Typer(no_args_is_help=True, help="Claude advisor (docs/api/coach.md)")
garmin = typer.Typer(no_args_is_help=True, help="Garmin sync (read-only towards Garmin)")
cli.add_typer(stats, name="stats")
cli.add_typer(garmin, name="garmin")
cli.add_typer(advisor, name="advisor")


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


engine = typer.Typer(no_args_is_help=True, help="Deterministic training engine")
engine_program = typer.Typer(
    no_args_is_help=True, help="Lifting program (data/program/program.yaml)"
)
engine_config = typer.Typer(no_args_is_help=True, help="Versioned engine config")
cli.add_typer(engine, name="engine")
engine.add_typer(engine_program, name="program")
engine.add_typer(engine_config, name="config")

_DATE = {"formats": ["%Y-%m-%d"]}


def _day(value: datetime | None) -> date | None:
    return value.date() if value else None


@engine.command("plan")
def engine_plan(
    day: Annotated[
        datetime | None, typer.Option("--date", **_DATE, help="day or week (default today)")
    ] = None,
    week: Annotated[bool, typer.Option("--week", help="whole week, not just the day")] = False,
    dry_run: Annotated[
        bool, typer.Option("--dry-run/--apply", help="default: write nothing")
    ] = True,
    verbose: Annotated[bool, typer.Option("--verbose/--quiet", help="print the decisions")] = True,
) -> None:
    """Compute the plan and print it with the decisions behind it (nothing is written)."""
    from app.engine import persist as P
    from app.engine import service
    from app.engine.render import render_week

    s = get_settings()
    today = service.user_today(s)
    target = _day(day) or today
    with session_scope() as db:
        try:
            if dry_run:
                plan, _cfg, _cv = service.plan_week(
                    db, today=today, week_start=service.monday(target), settings=s
                )
            else:
                P.reconcile(db, today)
                plan = service.apply_week(
                    db, today=today, week_start=service.monday(target), settings=s
                ).plan
        except FileNotFoundError as exc:
            raise _fail(exc) from None
        if not week:
            plan.days = [d for d in plan.days if d.day == target]
        typer.echo(render_week(plan, detail=verbose, decisions=verbose))
        if dry_run:
            db.rollback()
            typer.echo("(dry run: nothing written)")


@engine.command("run")
def engine_run(
    weeks: Annotated[int, typer.Option(help="weeks to plan, starting with the current one")] = 2,
    quiet: Annotated[bool, typer.Option("--quiet")] = False,
) -> None:
    """Apply: reconcile earlier plans, write planned sessions + decisions + advisor triggers."""
    from app.engine import service
    from app.engine.render import render_week

    s = get_settings()
    with session_scope() as db:
        try:
            summary = service.run_engine(db, weeks=weeks, settings=s)
        except FileNotFoundError as exc:
            raise _fail(exc) from None
        typer.echo(f"config {summary.config_version}; reconciled {summary.reconciled}")
        for wr in summary.weeks:
            r = wr.persisted
            typer.echo(
                f"week {wr.plan.week_start}: created {r.created}, updated {r.updated}, "
                f"unchanged {r.unchanged}, closed {r.skipped_closed}, re-proposed {r.reproposed}; "
                f"decisions +{r.decisions}, advisor triggers +{r.triggers}"
            )
            if not quiet:
                typer.echo(render_week(wr.plan, decisions=False))


@engine.command("readiness")
def engine_readiness(
    day: Annotated[datetime | None, typer.Option("--date", **_DATE)] = None,
) -> None:
    """Readiness colour + reason for a day (default today)."""
    from app.engine import service

    s = get_settings()
    d = _day(day) or service.user_today(s)
    with session_scope() as db:
        r = service.readiness_for(db, d, s)
    typer.echo(f"{r.day}: {r.color}{'' if r.known else ' (no data)'} - {r.reason}")
    for sig in r.signals:
        typer.echo(f"  {sig.name:6} {sig.level:7} {sig.detail}")


@engine.command("evaluate-runs")
def engine_evaluate_runs(
    weeks: Annotated[int, typer.Option(help="look back this many weeks")] = 6,
) -> None:
    """Evaluate recent runs: easy-run pass/fail, efficiency, HR drift, interval sessions."""
    from app.engine import service

    s = get_settings()
    with session_scope() as db:
        rows = service.evaluate_runs(db, today=service.user_today(s), weeks=weeks, settings=s)
    for r in rows:
        ev = r["evaluation"]
        if r["type"] == "intervals":
            typer.echo(
                f"{r['date']} intervals {ev['reps']} reps @ {ev['work_pace_s_per_km']:.0f}s/km, "
                f"work HR {ev['work_hr'] and round(ev['work_hr'])}, recovery drop {ev['recovery_drop_bpm'] and round(ev['recovery_drop_bpm'])} bpm, "  # noqa: E501
                f"{'PASS' if ev['passed'] else 'FAIL ' + '; '.join(ev['reasons'])}"
            )
        else:
            typer.echo(
                f"{r['date']} easy/long {r['km']:g} km avg HR {r['avg_hr'] and round(r['avg_hr'])}, "  # noqa: E501
                f"{'PASS' if ev['passed'] else 'FAIL ' + '; '.join(ev['reasons'])}, "
                f"EF {ev['efficiency']}, drift {ev['decoupling_pct']}%"
                f"{' FLAG' if ev['decoupling_flag'] else ''}, <=cap {ev['pct_at_or_below_cap'] and round(ev['pct_at_or_below_cap'])}%"  # noqa: E501
            )


@engine.command("triggers")
def engine_triggers(status: Annotated[str, typer.Option()] = "open") -> None:
    """List advisor triggers."""
    from app.db.models import AdvisorTrigger

    with session_scope() as db:
        for t in db.scalars(
            select(AdvisorTrigger)
            .where(AdvisorTrigger.status == status)
            .order_by(AdvisorTrigger.id)
        ):
            typer.echo(
                f"#{t.id} {t.created_at:%Y-%m-%d} {t.type} {t.subject_type}:{t.subject_id} {t.payload}"  # noqa: E501
            )


@engine_program.command("generate")
def engine_program_generate(
    weeks: Annotated[int, typer.Option(help="look back this many weeks")] = 8,
    write: Annotated[bool, typer.Option("--write/--print")] = True,
) -> None:
    """Derive data/program/program.yaml from the recent Upper/Lower sessions at the current gym."""
    from app.engine import service

    s = get_settings()
    with session_scope() as db:
        _program, text = service.generate_program(db, weeks=weeks, settings=s, write=write)
    typer.echo(text)
    if write:
        typer.echo(f"written to {s.program_path}")


@engine_config.command("show")
def engine_config_show() -> None:
    """The active config version and its lineage."""
    from app.engine import config_service as cs

    s = get_settings()
    with session_scope() as db:
        cs.ensure_seeded(db, s.engine_config_path)
        for v in cs.history(db):
            mark = "*" if v.is_active else " "
            typer.echo(
                f"{mark} {v.version:8} {v.author:7} {v.created_at:%Y-%m-%d %H:%M} {v.reason or ''}"
            )


@engine_config.command("rollback")
def engine_config_rollback(version: str) -> None:
    """Make an older version's content active again (as a new version)."""
    from app.engine import config_service as cs

    with session_scope() as db:
        try:
            row = cs.rollback(db, version, author="user")
        except cs.ConfigError as exc:
            raise _fail(exc) from None
        typer.echo(f"active: {row.version} ({row.reason})")


@engine_config.command("set")
def engine_config_set(
    path: Annotated[str, typer.Argument(help="dotted path, e.g. running.weekly_volume_floor_km")],
    value: Annotated[str, typer.Argument(help="YAML/JSON value, e.g. 18 or [7,12]")],
    reason: Annotated[str, typer.Option(help="why (stored on the new version)")],
    author: Annotated[str, typer.Option(help="engine | advisor | user")] = "user",
) -> None:
    """Create a new config version with one value changed (the active one is never edited)."""
    import yaml
    from pydantic import ValidationError

    from app.engine import config_service as cs

    s = get_settings()
    with session_scope() as db:
        cs.ensure_seeded(db, s.engine_config_path)
        try:
            row = cs.create_version(db, {path: yaml.safe_load(value)}, reason=reason, author=author)
        except (cs.ConfigError, ValidationError) as exc:
            raise _fail(exc) from None
        typer.echo(f"active: {row.version} ({row.author}) diff {row.diff}")


@engine_config.command("check-garmin")
def engine_config_check_garmin() -> None:
    """Apply Garmin's latest max HR / LTHR to the config now (the sync hook does this too)."""
    from app.engine import config_service as cs

    s = get_settings()
    with session_scope() as db:
        cs.ensure_seeded(db, s.engine_config_path)
        row = cs.apply_garmin_hr(db)
        typer.echo(
            f"new version {row.version}: {row.reason}" if row else "config already matches Garmin"
        )


@advisor.command("status")
def advisor_status() -> None:
    """Key configured? dry run? budget used this week, open triggers, waiting consultations."""
    from sqlalchemy import func

    from app.advisor import service
    from app.db.models import AdvisorConsultation, AdvisorTrigger

    s = get_settings()
    with session_scope() as db:
        st = service.coach_settings(db, s)
        typer.echo(
            f"model {s.advisor_model}; key {'set' if st.api_key_configured else 'NOT set'}; "
            f"dry run {st.dry_run}; approval {st.approval_mode}; "
            f"calls this week {st.calls_this_week}/{st.max_calls_per_week}"
        )
        for status, n in db.execute(
            select(AdvisorConsultation.status, func.count()).group_by(AdvisorConsultation.status)
        ):
            typer.echo(f"  consultations {status}: {n}")
        open_n = db.scalar(select(func.count()).where(AdvisorTrigger.status == "open"))
        typer.echo(f"  open engine triggers: {open_n}")


@advisor.command("run")
def advisor_run(
    what: Annotated[str, typer.Argument(help="weekly | triggers | retry | outcomes")],
) -> None:
    """Run an advisor job now (respects ADVISOR_DRY_RUN, the missing-key rule and the budget)."""
    from app.advisor import outcomes, service

    s = get_settings()
    with session_scope() as db:
        if what == "weekly":
            c = service.run_weekly_review(db, s)
        elif what == "triggers":
            c = service.run_trigger_batch(db, s)
        elif what == "retry":
            typer.echo(f"{service.retry_awaiting_key(db, s)} consultation(s) run")
            return
        elif what == "outcomes":
            typer.echo(f"{len(outcomes.run_outcome_check(db, s))} outcome(s) recorded")
            return
        else:
            raise _fail(ValueError("what must be weekly, triggers, retry or outcomes"))
        if c is None:
            typer.echo("nothing to do (already done this week, no open triggers, or over budget)")
        else:
            typer.echo(f"consultation {c.id}: {c.status}" + (f" ({c.error})" if c.error else ""))


@advisor.command("input")
def advisor_input() -> None:
    """Build the weekly-review input now and print its size per section (nothing is stored)."""
    from app.advisor import service, summary

    s = get_settings()
    with session_scope() as db:
        info = service.weekly_trigger_info(service.engine_service.monday(service.user_today(s)), [])
        doc = summary.build_input(db, trigger=info, question=None, settings=s)
    typer.echo(f"total ~{summary.estimate_tokens(doc)} tokens (chars / 4)")
    for key, value in doc.items():
        typer.echo(f"  {key:22} ~{summary.estimate_tokens(value)}")


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
