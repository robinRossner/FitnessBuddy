"""CLI: `uv run python -m apps.fitness.importers.strong {mapping|import}`."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Annotated

import typer

from apps.fitness.db.catalog import load_catalog
from apps.fitness.db.models import ImportRun
from apps.fitness.db.session import get_session_factory
from apps.fitness.importers.strong.importer import ImportOptions, fill_run, run_import, tables_exist
from apps.fitness.importers.strong.mapping import merge_mapping, read_mapping, write_mapping
from apps.fitness.importers.strong.parser import discover_files, parse_file
from apps.fitness.importers.strong.report import build_report, console_summary, write_report
from apps.fitness.settings import get_settings

cli = typer.Typer(add_completion=False, no_args_is_help=True, help="Strong CSV importer")

PathsOpt = Annotated[
    list[Path] | None,
    typer.Option("--path", "-p", help="CSV file or directory (repeatable). Default: imports/"),
]


@cli.command()
def mapping(paths: PathsOpt = None) -> None:
    """Refresh strong_mapping.csv: keep existing rows/edits, add new names as `unmapped`."""
    s = get_settings()
    catalog = load_catalog(s.catalog_path)
    names: set[str] = set()
    for f in discover_files(paths or [s.imports_dir]):
        pf = parse_file(f, default_weight_unit=s.strong_default_weight_unit)
        if pf.is_strong:
            names.update(r.exercise_name for wk in pf.workouts.values() for r in wk.rows)
    existing = read_mapping(s.strong_mapping_path)
    result = merge_mapping(existing, names, catalog)
    write_mapping(s.strong_mapping_path, result.rows)
    statuses: dict[str, int] = {}
    for r in result.rows.values():
        statuses[r.status] = statuses.get(r.status, 0) + 1
    typer.echo(f"mapping: {s.strong_mapping_path}")
    typer.echo(f"names in exports: {len(names)}; rows in mapping: {len(result.rows)} {statuses}")
    if result.added:
        typer.echo(f"added as unmapped ({len(result.added)}): " + ", ".join(result.added))
    if result.not_seen:
        typer.echo(
            f"in mapping but not in any export ({len(result.not_seen)}): "
            + ", ".join(result.not_seen)
        )


@cli.command("import")
def import_cmd(
    paths: PathsOpt = None,
    apply: Annotated[
        bool, typer.Option("--apply/--dry-run", help="Write to the DB (default: dry run)")
    ] = False,
    tz: Annotated[str | None, typer.Option(help="IANA tz of the Strong timestamps")] = None,
    report: Annotated[bool, typer.Option("--report/--no-report")] = True,
) -> None:
    """Import Strong exports. Dry run by default; `--apply` commits."""
    s = get_settings()
    opts = ImportOptions(
        paths=paths or [s.imports_dir],
        mapping_path=s.strong_mapping_path,
        catalog_path=s.catalog_path,
        engine_config_path=s.engine_config_path,
        tz=tz or s.user_tz,
        gym_name=s.default_gym_name,
        gyms_path=s.gyms_path,
        max_duration_s=int(s.strong_max_duration_h * 3600),
        apply=apply,
        default_weight_unit=s.strong_default_weight_unit,
        default_distance_unit=s.strong_default_distance_unit,
    )
    factory = get_session_factory()
    now = datetime.now()
    with factory() as db:
        if not tables_exist(db):
            typer.echo("DB has no tables. Run `uv run alembic upgrade head` first.", err=True)
            raise typer.Exit(2)
        res = run_import(db, opts)
        text = build_report(db, res, now)
        report_path = write_report(text, s.reports_dir, now) if report else None
        if apply and not res.error:
            run = db.get(ImportRun, res.import_run_id)
            if run is not None and report_path is not None:
                run.report_path = str(report_path)
            db.commit()
        else:
            db.rollback()
            # keep an audit row for dry runs / failed runs too
            run = fill_run(
                ImportRun(source="strong", mode="dry_run" if not apply else "apply"), res
            )
            run.status = "failed" if res.error else "ok"
            run.finished_at = datetime.now().astimezone()
            run.report_path = str(report_path) if report_path else None
            db.add(run)
            db.commit()
    typer.echo(console_summary(res))
    if report_path:
        typer.echo(f"report: {report_path}")
    if res.error:
        raise typer.Exit(1)


if __name__ == "__main__":
    cli()
