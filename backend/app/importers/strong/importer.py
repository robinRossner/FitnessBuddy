"""Strong CSV -> DB import. Idempotent (deterministic UUIDv5 ids) and dry-run by default."""

from __future__ import annotations

import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from sqlalchemy import inspect, select
from sqlalchemy.orm import Session

from app.db.catalog import CatalogExercise, load_catalog, sync_catalog
from app.db.models import (
    Exercise,
    ExerciseNameMapping,
    ExerciseVariant,
    Gym,
    ImportRun,
    TrainingSession,
    WorkoutSet,
)
from app.db.seed import ensure_config_version, ensure_default_gym
from app.db.types import EquipmentType, Fidelity, LoadConvention, MappingStatus, Source, utcnow
from app.engine.config import load_engine_config
from app.importers.strong.mapping import MappingRow, read_mapping, validate_mapping
from app.importers.strong.parser import (
    ParsedFile,
    SkippedRow,
    StrongRow,
    Workout,
    derive_session_kind,
    discover_files,
    parse_file,
    to_utc,
)
from app.stats import rebuild_stats

# Fixed namespace: ids must never change, or re-imports would duplicate data.
NAMESPACE = uuid.UUID("6f1c2b7e-3a59-4d2e-9b8f-5e7a1c0d4f21")
GENERIC_EQUIPMENT = {EquipmentType.BARBELL, EquipmentType.DUMBBELL, EquipmentType.BODYWEIGHT}
LADDER_EQUIPMENT = {EquipmentType.MACHINE_STACK, EquipmentType.CABLE}
SOURCE_NAME = "strong"


def session_uuid(start_local: datetime) -> str:
    return str(uuid.uuid5(NAMESPACE, f"strong:session:{start_local.isoformat()}"))


def set_uuid(start_local: datetime, exercise: str, occurrence: int, position: int) -> str:
    key = f"strong:set:{start_local.isoformat()}|{exercise}|{occurrence}|{position}"
    return str(uuid.uuid5(NAMESPACE, key))


@dataclass
class ImportOptions:
    paths: list[Path]
    mapping_path: Path
    catalog_path: Path
    engine_config_path: Path
    tz: str = "Europe/Vienna"
    gym_name: str = "Main gym"
    apply: bool = False
    default_weight_unit: str = "kg"
    default_distance_unit: str = "km"


@dataclass
class FileInfo:
    name: str
    rows_read: int
    workouts: int
    min_date: datetime | None
    max_date: datetime | None
    delimiter: str
    is_strong: bool
    workouts_used: int = 0


@dataclass
class ImportResult:
    apply: bool
    files: list[FileInfo] = field(default_factory=list)
    rows_read: int = 0
    rows_imported: int = 0
    skipped: list[SkippedRow] = field(default_factory=list)
    skip_reasons: Counter[str] = field(default_factory=Counter)
    sessions_total: int = 0
    sessions_created: int = 0
    sessions_updated: int = 0
    sessions_unchanged: int = 0
    sessions_tombstoned_skipped: int = 0
    sets_created: int = 0
    sets_updated: int = 0
    sets_unchanged: int = 0
    sets_tombstoned: int = 0
    overlapping_workouts: int = 0
    overlapping_differing: int = 0
    date_min: datetime | None = None
    date_max: datetime | None = None
    unmapped_names: Counter[str] = field(default_factory=Counter)
    auto_names: Counter[str] = field(default_factory=Counter)
    mapping_errors: list[str] = field(default_factory=list)
    mapping: dict[str, MappingRow] = field(default_factory=dict)
    workouts: list[Workout] = field(default_factory=list)  # selected workouts
    dst_flags: list[tuple[datetime, str]] = field(default_factory=list)
    row_issues: list[tuple[StrongRow, str]] = field(default_factory=list)
    ladders: dict[str, list[float]] = field(default_factory=dict)
    stats_rows: int = 0
    import_run_id: int | None = None
    report_path: Path | None = None
    error: str | None = None


# --------------------------------------------------------------------------- selection


def select_workouts(parsed: list[ParsedFile]) -> tuple[dict[datetime, Workout], int, int]:
    """Pick one version per workout start. Newest export (latest max date) wins.

    Returns (start -> workout, overlapping count, overlapping-with-different-content count).
    Tie-break: file modification time, then file name.
    """
    candidates: dict[datetime, list[tuple[tuple, Workout]]] = defaultdict(list)
    for pf in parsed:
        if not pf.is_strong:
            continue
        rank = (pf.max_date or datetime.min, pf.path.stat().st_mtime, pf.path.name)
        for start, wk in pf.workouts.items():
            candidates[start].append((rank, wk))
    chosen: dict[datetime, Workout] = {}
    overlapping = differing = 0
    for start, cands in candidates.items():
        cands.sort(key=lambda c: c[0], reverse=True)
        chosen[start] = cands[0][1]
        if len(cands) > 1:
            overlapping += 1
            if len({c[1].content_key() for c in cands}) > 1:
                differing += 1
    return dict(sorted(chosen.items())), overlapping, differing


# --------------------------------------------------------------------------- DB helpers


def _sync_variants(
    db: Session,
    mapping: dict[str, MappingRow],
    exercises: dict[str, Exercise],
    gym: Gym,
) -> dict[str, ExerciseVariant]:
    """Create/update variants + exercise_name_mappings rows. Returns strong_name -> variant."""
    by_key: dict[tuple[str, int | None], ExerciseVariant] = {
        (v.name, v.gym_id): v for v in db.scalars(select(ExerciseVariant))
    }
    name_rows = {
        m.source_name: m
        for m in db.scalars(
            select(ExerciseNameMapping).where(ExerciseNameMapping.source == SOURCE_NAME)
        )
    }
    out: dict[str, ExerciseVariant] = {}
    for row in mapping.values():
        variant: ExerciseVariant | None = None
        exercise = exercises.get(row.canonical_exercise)
        if row.importable and exercise is not None:
            eq = EquipmentType(row.equipment_type)
            gym_id = None if eq in GENERIC_EQUIPMENT else gym.id
            variant = by_key.get((row.variant_name, gym_id))
            if variant is None:
                variant = ExerciseVariant(name=row.variant_name, gym_id=gym_id)
                db.add(variant)
                by_key[(row.variant_name, gym_id)] = variant
            variant.exercise = exercise
            variant.equipment_type = eq
            variant.load_convention = LoadConvention(row.load_convention)
            out[row.strong_name] = variant
        nm = name_rows.get(row.strong_name)
        if nm is None:
            nm = ExerciseNameMapping(source=SOURCE_NAME, source_name=row.strong_name)
            db.add(nm)
        nm.status = MappingStatus(row.status)
        nm.exercise_id = exercise.id if exercise is not None else None
        nm.notes = row.notes or None
        db.flush()
        nm.variant_id = variant.id if variant is not None else None
    db.flush()
    return out


def _infer_ladders(db: Session, variants: set[ExerciseVariant]) -> dict[str, list[float]]:
    """Ladder for stack/cable variants = sorted distinct historical weights (> 0).

    Only touches variants whose ladder is empty or was inferred before (user edits win).
    """
    out: dict[str, list[float]] = {}
    for v in variants:
        if v.equipment_type not in LADDER_EQUIPMENT:
            continue
        if v.weight_ladder and not v.ladder_inferred:
            continue
        weights = db.scalars(
            select(WorkoutSet.weight_kg)
            .where(
                WorkoutSet.variant_id == v.id,
                WorkoutSet.deleted_at.is_(None),
                WorkoutSet.weight_kg > 0,
            )
            .distinct()
        ).all()
        ladder = sorted({float(w) for w in weights} | {float(w) for w in (v.weight_ladder or [])})
        if ladder:
            v.weight_ladder = ladder
            v.ladder_inferred = True
            out[v.name] = ladder
    db.flush()
    return out


def _assign(obj, values: dict) -> bool:
    changed = False
    for k, v in values.items():
        if getattr(obj, k) != v:
            setattr(obj, k, v)
            changed = True
    return changed


# --------------------------------------------------------------------------- main entry


def run_import(db: Session, opts: ImportOptions) -> ImportResult:
    """Run the full import inside `db`'s transaction. Caller commits (apply) or rolls back."""
    res = ImportResult(apply=opts.apply)
    catalog: dict[str, CatalogExercise] = load_catalog(opts.catalog_path)
    mapping = read_mapping(opts.mapping_path)
    res.mapping = mapping
    res.mapping_errors = validate_mapping(mapping, catalog)
    if res.mapping_errors:
        res.error = "mapping file has errors; fix them before importing"
        return res
    cfg = load_engine_config(opts.engine_config_path)

    # ---- parse
    parsed: list[ParsedFile] = []
    for path in discover_files(opts.paths):
        pf = parse_file(
            path,
            default_weight_unit=opts.default_weight_unit,
            default_distance_unit=opts.default_distance_unit,
        )
        parsed.append(pf)
        res.files.append(
            FileInfo(
                name=str(path.name),
                rows_read=pf.rows_read,
                workouts=len(pf.workouts),
                min_date=pf.min_date,
                max_date=pf.max_date,
                delimiter=pf.delimiter,
                is_strong=pf.is_strong,
            )
        )
        if pf.is_strong:
            res.rows_read += pf.rows_read
            res.skipped.extend(pf.skipped)
    chosen, res.overlapping_workouts, res.overlapping_differing = select_workouts(parsed)
    res.workouts = list(chosen.values())

    used_by_file = Counter(wk.source_file for wk in chosen.values())
    for fi in res.files:
        fi.workouts_used = used_by_file.get(fi.name, 0)
    for pf in parsed:
        for start, wk in pf.workouts.items():
            if chosen.get(start) is not wk:
                res.skipped.extend(
                    SkippedRow(
                        pf.path.name, r.line_no, "superseded by newer export", r.exercise_name
                    )
                    for r in wk.rows
                )

    # ---- reference data
    gym = ensure_default_gym(db, opts.gym_name, opts.tz)
    ensure_config_version(db, opts.engine_config_path)
    exercises = sync_catalog(db, catalog)
    variants = _sync_variants(db, mapping, exercises, gym)

    # ---- sessions + sets
    run = ImportRun(source=SOURCE_NAME, mode="apply" if opts.apply else "dry_run")
    db.add(run)
    db.flush()
    session_ids = [session_uuid(s) for s in chosen]
    existing_sessions: dict[str, TrainingSession] = {}
    existing_sets: dict[str, WorkoutSet] = {}
    sets_by_session: dict[str, list[WorkoutSet]] = defaultdict(list)
    for i in range(0, len(session_ids), 500):
        chunk = session_ids[i : i + 500]
        existing_sessions.update(
            (s.id, s)
            for s in db.scalars(select(TrainingSession).where(TrainingSession.id.in_(chunk)))
        )
        existing_sets.update(
            (s.id, s)
            for s in db.scalars(select(WorkoutSet).where(WorkoutSet.session_id.in_(chunk)))
        )
    for ws in existing_sets.values():
        sets_by_session[ws.session_id].append(ws)
    seen_set_ids: set[str] = set()
    touched_variants: set[ExerciseVariant] = set()
    now = utcnow()

    for start, wk in chosen.items():
        sid = session_uuid(start)
        utc, dst_flag = to_utc(start, opts.tz)
        if dst_flag:
            res.dst_flags.append((start, dst_flag))
        values = {
            "name": wk.name,
            "session_kind": derive_session_kind(wk.name),
            "started_at_local": start,
            "tz": opts.tz,
            "started_at_utc": utc,
            "duration_s": wk.duration_s,
            "notes": wk.notes,
            "fidelity": Fidelity.LOW if any(r.is_cardio for r in wk.rows) else Fidelity.HIGH,
        }
        sess = existing_sessions.get(sid)
        if sess is not None and sess.deleted_at is not None:
            res.sessions_tombstoned_skipped += 1  # user deleted it: respect the tombstone
            res.skipped.extend(
                SkippedRow(wk.source_file, r.line_no, "session deleted in app", r.exercise_name)
                for r in wk.rows
            )
            continue
        session_changed = False
        if sess is None:
            sess = TrainingSession(
                id=sid,
                source=Source.STRONG_IMPORT,
                source_ref=wk.source_file,
                gym_id=gym.id,  # only on create: a later gym reassignment by the user is kept
                import_run_id=run.id,
                **values,
            )
            db.add(sess)
            res.sessions_created += 1
            session_is_new = True
        else:
            session_changed = _assign(sess, values)
            sess.source_ref = wk.source_file  # provenance only; not counted as a change
            session_is_new = False

        for row in wk.rows:
            for issue in row.issues:
                res.row_issues.append((row, issue))
            m = mapping.get(row.exercise_name)
            if m is None or not m.importable or row.exercise_name not in variants:
                res.unmapped_names[row.exercise_name] += 1
                res.skipped.append(
                    SkippedRow(wk.source_file, row.line_no, "exercise unmapped", row.exercise_name)
                )
                continue
            if m.status == MappingStatus.AUTO:
                res.auto_names[row.exercise_name] += 1
            variant = variants[row.exercise_name]
            touched_variants.add(variant)
            set_id = set_uuid(start, row.exercise_name, row.block_occurrence, row.set_index)
            seen_set_ids.add(set_id)
            set_values = {
                "session_id": sid,
                "variant_id": variant.id,
                "source_exercise_name": row.exercise_name,
                "exercise_order": row.exercise_order,
                "block_occurrence": row.block_occurrence,
                "set_index": row.set_index,
                "set_type": row.set_type,
                "weight_kg": row.weight_kg,
                "reps": row.reps,
                "duration_s": row.duration_hold_s,
                "distance_m": row.distance_m,
                "rpe": row.rpe,
                "fidelity": Fidelity.LOW if row.is_cardio else Fidelity.HIGH,
                "notes": row.notes,
                "deleted_at": None,
            }
            ws = existing_sets.get(set_id)
            if ws is None:
                db.add(WorkoutSet(id=set_id, source=Source.STRONG_IMPORT, **set_values))
                res.sets_created += 1
            elif _assign(ws, set_values):
                res.sets_updated += 1
                session_changed = True
            else:
                res.sets_unchanged += 1
            res.rows_imported += 1

        # sets of this session that are no longer in the chosen export -> tombstone
        for ws in sets_by_session.get(sid, []):
            stale = ws.id not in seen_set_ids and ws.deleted_at is None
            if stale and ws.source == Source.STRONG_IMPORT:
                ws.deleted_at = now
                res.sets_tombstoned += 1
                session_changed = True
        if not session_is_new:
            if session_changed:
                sess.import_run_id = run.id
                res.sessions_updated += 1
            else:
                res.sessions_unchanged += 1
        res.sessions_total += 1

    if chosen:
        res.date_min, res.date_max = min(chosen), max(chosen)
    db.flush()

    # ---- derived data
    res.ladders = _infer_ladders(db, touched_variants)
    res.stats_rows = rebuild_stats(
        db,
        bodyweight_kg=cfg.user.bodyweight_kg,
        max_reps=cfg.strength.e1rm.max_reps,
        min_reps=cfg.strength.e1rm.min_reps,
    )

    for s in res.skipped:
        res.skip_reasons[s.reason] += 1

    fill_run(run, res)
    run.status = "ok"
    run.finished_at = utcnow()
    db.flush()
    res.import_run_id = run.id
    return res


def fill_run(run: ImportRun, res: ImportResult) -> ImportRun:
    run.files = [
        {
            "name": f.name,
            "rows": f.rows_read,
            "workouts": f.workouts,
            "used": f.workouts_used,
            "max_date": f.max_date.isoformat() if f.max_date else None,
            "strong": f.is_strong,
        }
        for f in res.files
    ]
    run.rows_read = res.rows_read
    run.rows_imported = res.rows_imported
    run.rows_skipped = len(res.skipped)
    run.sessions_created = res.sessions_created
    run.sessions_updated = res.sessions_updated
    run.sessions_unchanged = res.sessions_unchanged
    run.summary = {
        "skip_reasons": dict(res.skip_reasons),
        "sets_created": res.sets_created,
        "sets_updated": res.sets_updated,
        "sets_unchanged": res.sets_unchanged,
        "sets_tombstoned": res.sets_tombstoned,
        "overlapping_workouts": res.overlapping_workouts,
        "overlapping_differing": res.overlapping_differing,
        "unmapped_names": dict(res.unmapped_names),
        "error": res.error,
    }
    return run


def tables_exist(db: Session) -> bool:
    return inspect(db.get_bind()).has_table("sessions")
