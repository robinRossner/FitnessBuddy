"""Strong CSV -> DB import. Idempotent (deterministic UUIDv5 ids) and dry-run by default."""

from __future__ import annotations

import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from sqlalchemy import delete, inspect, select
from sqlalchemy.orm import Session

from apps.fitness.db.catalog import CatalogExercise, load_catalog, sync_catalog
from apps.fitness.db.models import (
    Exercise,
    ExerciseNameMapping,
    ExerciseSessionStats,
    ExerciseVariant,
    Gym,
    ImportRun,
    PlannedSession,
    TrainingSession,
    WorkoutSet,
)
from apps.fitness.db.seed import ensure_config_version
from apps.fitness.db.types import (
    EquipmentType,
    Fidelity,
    LoadConvention,
    MappingStatus,
    SessionKind,
    SetType,
    Source,
    utcnow,
)
from apps.fitness.engine.config import load_engine_config
from apps.fitness.exercise_service import is_user_generic_variant
from apps.fitness.gym_service import is_user_variant
from apps.fitness.gyms import GYM_BOUND_EQUIPMENT, GymPeriods, GymSpec, load_gyms
from apps.fitness.importers.strong.mapping import MappingRow, read_mapping, validate_mapping
from apps.fitness.importers.strong.parser import (
    ParsedFile,
    SkippedRow,
    StrongRow,
    Workout,
    derive_session_kind,
    discover_files,
    parse_file,
    to_utc,
)
from apps.fitness.session_kind import ExerciseUse, infer_session_kind
from apps.fitness.stats import rebuild_stats

# Fixed namespace: ids must never change, or re-imports would duplicate data.
NAMESPACE = uuid.UUID("6f1c2b7e-3a59-4d2e-9b8f-5e7a1c0d4f21")
LADDER_EQUIPMENT = {EquipmentType.MACHINE_STACK, EquipmentType.CABLE}
SOURCE_NAME = "strong"


def session_uuid(start_local: datetime) -> str:
    return str(uuid.uuid5(NAMESPACE, f"strong:session:{start_local.isoformat()}"))


def variant_uuid(base_name: str, gym_key: str | None) -> str:
    """Stable identity of a variant: mapping variant name + (for gym-bound equipment) gym key."""
    return str(uuid.uuid5(NAMESPACE, f"strong:variant:{base_name}|{gym_key or ''}"))


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
    gym_name: str = "Main gym"  # used when there is no gyms file: one gym for everything
    gyms_path: Path | None = None
    max_duration_s: int = 4 * 3600  # longer sessions are bad data: duration stored as NULL
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
    sets_moved: int = 0  # existing sets re-pointed to another variant (gym change)
    variants_removed: int = 0  # orphaned variants deleted (no sets left)
    duration_corrected: list[tuple[datetime, str | None, int]] = field(default_factory=list)
    max_duration_s: int = 4 * 3600
    anomaly_counts: dict[str, int] = field(default_factory=dict)  # filled by build_report
    kinds_inferred: Counter[str] = field(default_factory=Counter)
    sessions_by_gym: Counter[str] = field(default_factory=Counter)
    gym_ranges: list[tuple[str, date | None, str | None]] = field(default_factory=list)
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


def _sync_gyms(db: Session, periods: GymPeriods, default_tz: str) -> dict[str, Gym]:
    """Upsert the canonical gyms. Identity = key (renames follow); legacy rows adopt by name."""
    rows = list(db.scalars(select(Gym)))
    by_key = {g.key: g for g in rows if g.key}
    by_name = {g.name: g for g in rows}
    out: dict[str, Gym] = {}
    for spec in periods.canonical_gyms:
        gym = by_key.get(spec.key)
        if gym is None:
            gym = by_name.get(spec.name)
            if gym is not None and gym.key not in (None, spec.key):
                gym = None  # same name but another gym's identity: do not steal it
        if gym is None:
            gym = Gym(name=spec.name)
            db.add(gym)
        gym.key = spec.key
        gym.name = spec.name
        gym.timezone = spec.timezone or gym.timezone or default_tz
        if spec.notes:
            gym.notes = spec.notes
        gym.deleted_at = None
        out[spec.key] = gym
    db.flush()
    return out


def _retire_gyms(db: Session, keep: set[int], legacy_name: str) -> None:
    """Tombstone gyms the importer used to manage but that no longer exist (merged / removed)."""
    for gym in db.scalars(select(Gym).where(Gym.deleted_at.is_(None))):
        if gym.id in keep or not (gym.key or gym.name == legacy_name):
            continue
        referenced = any(
            db.scalar(select(col).where(fk == gym.id, *extra).limit(1)) is not None
            for col, fk, extra in (
                (TrainingSession.id, TrainingSession.gym_id, ()),
                # tombstoned variants do not keep a gym alive
                (
                    ExerciseVariant.id,
                    ExerciseVariant.gym_id,
                    (ExerciseVariant.deleted_at.is_(None),),
                ),
                (PlannedSession.id, PlannedSession.gym_id, ()),
            )
        )
        if not referenced:
            gym.deleted_at = utcnow()


class VariantResolver:
    """strong mapping row + gym -> ExerciseVariant, created on first use (deterministic key)."""

    def __init__(
        self,
        db: Session,
        exercises: dict[str, Exercise],
        gyms: dict[str, Gym],
        *,
        multi_gym: bool,
    ):
        self.db = db
        self.exercises = exercises
        self.gyms = gyms
        self.multi_gym = multi_gym
        existing = list(db.scalars(select(ExerciseVariant)))
        self.by_key = {v.key: v for v in existing if v.key}
        # adoptable by (name, gym): variants from before keys existed, and machines the user set
        # up in the app (apps/fitness/gym_service.py) before the same machine showed up in an import
        self.legacy = {
            (v.name, v.gym_id): v
            for v in existing
            if not v.key or is_user_variant(v) or is_user_generic_variant(v)
        }

    def get(self, m: MappingRow, gym: GymSpec) -> ExerciseVariant:
        eq = EquipmentType(m.equipment_type)
        bound = eq.value in GYM_BOUND_EQUIPMENT
        gym_row = self.gyms[gym.key] if bound else None
        key = variant_uuid(m.variant_name, gym.key if bound else None)
        name = (
            f"{m.variant_name} @ {gym_row.name}"
            if gym_row is not None and self.multi_gym
            else m.variant_name
        )
        v = self.by_key.get(key)
        if v is None:  # a variant from before keys existed: adopt it
            v = self.legacy.pop((name, gym_row.id if gym_row else None), None)
        if v is None:
            v = ExerciseVariant()
            self.db.add(v)
        # a variant the user renamed in the app keeps its name (the gym suffix then stays as it was)
        renamed = (
            v.id is not None
            and v.exercise is not None
            and v.exercise.edited_by_user
            and v.name.split(" @ ")[0] != m.variant_name
        )
        v.deleted_at = None  # revive a tombstoned variant that is in use again
        v.key = key
        if not renamed:
            v.name = name
        v.gym_id = gym_row.id if gym_row else None
        v.exercise = self.exercises[m.canonical_exercise]
        v.equipment_type = eq
        v.load_convention = LoadConvention(m.load_convention)
        v.active = m.active
        self.by_key[key] = v
        if v.id is None:
            self.db.flush()
        return v


def _sync_name_mappings(
    db: Session,
    mapping: dict[str, MappingRow],
    exercises: dict[str, Exercise],
    latest_variant: dict[str, ExerciseVariant],
) -> None:
    """Mirror strong_mapping.csv into exercise_name_mappings. For gym-bound exercises the
    variant is the one of the most recent session (the gym you train at now)."""
    name_rows = {
        m.source_name: m
        for m in db.scalars(
            select(ExerciseNameMapping).where(ExerciseNameMapping.source == SOURCE_NAME)
        )
    }
    for row in mapping.values():
        exercise = exercises.get(row.canonical_exercise)
        nm = name_rows.get(row.strong_name)
        if nm is None:
            nm = ExerciseNameMapping(source=SOURCE_NAME, source_name=row.strong_name)
            db.add(nm)
        nm.status = MappingStatus(row.status)
        nm.exercise_id = exercise.id if exercise is not None else None
        nm.notes = row.notes or None
        variant = latest_variant.get(row.strong_name) if row.importable else None
        nm.variant_id = variant.id if variant is not None else None
    db.flush()


def _remove_orphan_variants(db: Session, mapping: dict[str, MappingRow]) -> int:
    """Tombstone (deleted_at) importer-made variants that no set / mapping refers to any more
    (e.g. the single 'Lat Pulldown (Cable)' from before gym periods, or a merged gym's variants).
    Soft delete so sync clients learn about it; the resolver revives a variant if it is needed."""
    bases = {m.variant_name for m in mapping.values() if m.variant_name}
    used = set(db.scalars(select(WorkoutSet.variant_id).distinct()))
    used |= set(
        db.scalars(
            select(ExerciseNameMapping.variant_id).where(
                ExerciseNameMapping.variant_id.is_not(None)
            )
        )
    )
    removed = 0
    for v in db.scalars(select(ExerciseVariant).where(ExerciseVariant.deleted_at.is_(None))):
        if (
            v.id in used
            or v.name.split(" @ ")[0] not in bases
            or is_user_variant(v)
            or is_user_generic_variant(v)
        ):
            continue  # variants made in the app are the user's, never importer leftovers
        db.execute(delete(ExerciseSessionStats).where(ExerciseSessionStats.variant_id == v.id))
        v.deleted_at = utcnow()
        removed += 1
    db.flush()
    return removed


def _infer_ladders(db: Session) -> dict[str, list[float]]:
    """Ladder per stack/cable variant (so per gym) = sorted distinct historical weights (> 0).

    Only touches variants whose ladder is empty or was inferred before (user edits win); an
    inferred ladder is rebuilt from the current sets, so sets moved to another gym leave it.
    """
    out: dict[str, list[float]] = {}
    variants = db.scalars(
        select(ExerciseVariant).where(
            ExerciseVariant.equipment_type.in_([e.value for e in LADDER_EQUIPMENT])
        )
    )
    for v in variants:
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
        ladder = sorted({float(w) for w in weights})
        if ladder:
            if v.weight_ladder != ladder:
                v.weight_ladder = ladder
            v.ladder_inferred = True
            out[v.name] = ladder
    db.flush()
    return out


def _exercise_uses(
    wk: Workout, mapping: dict[str, MappingRow], catalog: dict[str, CatalogExercise]
) -> list[ExerciseUse]:
    """One entry per non-warm-up set of an importable exercise, for session-kind inference."""
    uses = []
    for r in wk.rows:
        m = mapping.get(r.exercise_name)
        cat = catalog.get(m.canonical_exercise) if m is not None and m.importable else None
        if cat is None or r.set_type == SetType.WARMUP:
            continue
        uses.append(
            ExerciseUse(
                cat.movement_pattern,
                cat.primary_muscle,
                bodyweight=m.equipment_type == EquipmentType.BODYWEIGHT.value,
            )
        )
    return uses


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
    res = ImportResult(apply=opts.apply, max_duration_s=opts.max_duration_s)
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
    if opts.gyms_path is not None and opts.gyms_path.exists():
        periods = load_gyms(opts.gyms_path)
    else:
        periods = GymPeriods.single(opts.gym_name)
    gyms = _sync_gyms(db, periods, opts.tz)
    res.gym_ranges = [
        (g.name, g.start, (f"same as {g.same_as}" if g.same_as else None)) for g in periods.specs
    ]
    ensure_config_version(db, opts.engine_config_path)
    exercises = sync_catalog(db, catalog)
    resolver = VariantResolver(db, exercises, gyms, multi_gym=len(periods.canonical_gyms) > 1)
    latest_variant: dict[str, tuple[datetime, ExerciseVariant]] = {}

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
    now = utcnow()

    for start, wk in chosen.items():
        sid = session_uuid(start)
        utc, dst_flag = to_utc(start, opts.tz)
        if dst_flag:
            res.dst_flags.append((start, dst_flag))
        gym_spec = periods.resolve(start.date())
        gym_row = gyms[gym_spec.key]
        res.sessions_by_gym[gym_row.name] += 1
        duration_s = wk.duration_s
        if duration_s and duration_s > opts.max_duration_s:  # bad data (e.g. timer left running)
            res.duration_corrected.append((start, wk.name, duration_s))
            duration_s = None
        kind = derive_session_kind(wk.name)
        if kind is SessionKind.OTHER:  # generic name ("Morning Workout"): look at the exercises
            kind = infer_session_kind(_exercise_uses(wk, mapping, catalog))
            res.kinds_inferred[kind.value] += 1
        values = {
            "name": wk.name,
            "session_kind": kind,
            "gym_id": gym_row.id,  # gyms.yaml is the source of truth for imported sessions
            "started_at_local": start,
            "tz": opts.tz,
            "started_at_utc": utc,
            "duration_s": duration_s,
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
            if m is None or not m.importable or m.canonical_exercise not in exercises:
                res.unmapped_names[row.exercise_name] += 1
                res.skipped.append(
                    SkippedRow(wk.source_file, row.line_no, "exercise unmapped", row.exercise_name)
                )
                continue
            if m.status == MappingStatus.AUTO:
                res.auto_names[row.exercise_name] += 1
            variant = resolver.get(m, gym_spec)
            prev = latest_variant.get(row.exercise_name)
            if prev is None or start >= prev[0]:
                latest_variant[row.exercise_name] = (start, variant)
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
            else:
                moved = ws.variant_id != variant.id
                if _assign(ws, set_values):
                    res.sets_moved += moved
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

    # ---- name mappings, derived data, cleanup
    _sync_name_mappings(db, mapping, exercises, {n: v for n, (_, v) in latest_variant.items()})
    res.ladders = _infer_ladders(db)
    res.stats_rows = rebuild_stats(
        db,
        bodyweight_kg=cfg.user.bodyweight_kg,
        max_reps=cfg.strength.e1rm.max_reps,
        min_reps=cfg.strength.e1rm.min_reps,
    )
    res.variants_removed = _remove_orphan_variants(db, mapping)
    _retire_gyms(db, {g.id for g in gyms.values()}, opts.gym_name)
    db.flush()

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
        "sets_moved": res.sets_moved,
        "variants_removed": res.variants_removed,
        "durations_corrected": len(res.duration_corrected),
        "sessions_by_gym": dict(res.sessions_by_gym),
        "overlapping_workouts": res.overlapping_workouts,
        "overlapping_differing": res.overlapping_differing,
        "unmapped_names": dict(res.unmapped_names),
        "error": res.error,
    }
    return run


def tables_exist(db: Session) -> bool:
    return inspect(db.get_bind()).has_table("sessions")
