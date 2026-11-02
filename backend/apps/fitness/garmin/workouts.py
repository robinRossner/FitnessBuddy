"""Planned runs -> Garmin Connect workouts, scheduled on their day, so they reach the watch.

The official Personal API is pull-only, so this uses python-garminconnect (GARMIN_EMAIL /
GARMIN_PASSWORD) through the same login as the sync fallback (`GarminConnectLogin`; a failed
login blocks new attempts for hours, Garmin SSO 429s repeated logins). Contract and settings:
docs/api/garmin.md.

Stateless ownership: every workout we create carries a marker in its description,
`[FitnessBuddy ps=<planned session id> d=<YYYY-MM-DD> h=<content hash>]`. A push lists the Garmin
workout library, keeps only workouts with that marker (anything else is never touched) and
reconciles them with the open planned runs:

- an open run (status proposed/approved, no run activity that day) in the window (today +
  `garmin_push_days`) without a workout -> upload + schedule on its date;
- a workout whose session, date or content changed (readiness made it easier, coach moved it,
  builder changed) -> upload the new one, schedule it, delete the old one;
- a workout whose day is before today, whose run was done (activity that day / session closed)
  or whose planned session no longer exists or is no longer a run -> unschedule + delete.

Open future runs we already pushed outside the window (the app's "Send to watch" on a later day)
stay and are kept up to date. Nothing is stored in the database; the push is idempotent (a second
run reports `unchanged`).
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from garminconnect.workout import (
    ConditionType,
    ExecutableStep,
    RepeatGroup,
    RunningWorkout,
    StepType,
    TargetType,
    WorkoutSegment,
)
from sqlalchemy import select
from sqlalchemy.orm import Session

from apps.fitness.db.models import PlannedSession, Run
from apps.fitness.garmin.errors import GarminError, NotFoundError, SourceUnavailable
from apps.fitness.garmin.mapping import RUN_TYPES
from apps.fitness.garmin.python_garminconnect import shared_login, translate_error
from apps.fitness.settings import Settings, get_settings

log = logging.getLogger(__name__)

BUILDER_VERSION = 1  # bump when the workout JSON changes: every pushed workout gets replaced
RUN_KINDS = ("easy_run", "intervals", "long_run")
OPEN_STATUSES = ("proposed", "approved")
HORIZON_DAYS = 21  # how far ahead pushed-on-request workouts are kept up to date
NAME_MAX = 32  # Garmin accepts longer names; the watch list shows ~15-20 characters
DESCRIPTION_MAX = 480
PAST_LOOKBACK_DAYS = 60  # schedule entries of our old workouts are looked up this far back
MARKER_RE = re.compile(
    r"\[FitnessBuddy ps=(?P<pid>[0-9a-fA-F-]{36}) d=(?P<day>\d{4}-\d{2}-\d{2}) "
    r"h=(?P<hash>[0-9a-f]{12})\]"
)
NAME_RE = re.compile(r"^(Mon|Tue|Wed|Thu|Fri|Sat|Sun) \d{2}\.\d{2} ")
WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
INTERVAL_LABELS = {"threshold": "Thr", "vo2max": "VO2", "race_5k": "5K"}


# ---------------------------------------------------------------------------- plain data


@dataclass(frozen=True)
class PlannedRun:
    planned_session_id: str
    day: date
    kind: str
    title: str | None
    session: dict[str, Any]


@dataclass
class WorkoutSpec:
    run: PlannedRun
    name: str
    content_hash: str
    workout: RunningWorkout

    def payload(self) -> dict[str, Any]:
        return self.workout.to_dict()


@dataclass
class RemoteWorkout:
    workout_id: int
    name: str
    planned_session_id: str
    day: date
    content_hash: str
    schedules: list[tuple[int, str]] = field(default_factory=list)  # (schedule id, date)


@dataclass
class PushItem:
    date: str
    action: str  # uploaded | updated | unchanged | scheduled | deleted | failed
    name: str | None = None
    planned_session_id: str | None = None
    workout_id: int | None = None
    reason: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "date": self.date,
            "action": self.action,
            "name": self.name,
            "planned_session_id": self.planned_session_id,
            "workout_id": self.workout_id,
            "reason": self.reason,
        }


@dataclass
class PushReport:
    dry_run: bool
    window_from: date
    window_to: date
    items: list[PushItem] = field(default_factory=list)
    workouts: list[WorkoutSpec] = field(default_factory=list)  # what we want on the watch
    remote_checked: bool = True

    @property
    def counts(self) -> dict[str, int]:
        out = {k: 0 for k in ("uploaded", "updated", "unchanged", "scheduled", "deleted", "failed")}
        for it in self.items:
            out[it.action] = out.get(it.action, 0) + 1
        return out

    def as_dict(self) -> dict[str, Any]:
        days: dict[str, list[dict[str, Any]]] = {}
        for it in sorted(self.items, key=lambda i: (i.date, i.action)):
            days.setdefault(it.date, []).append(it.as_dict())
        return {
            "dry_run": self.dry_run,
            "remote_checked": self.remote_checked,
            "window": {"from": self.window_from.isoformat(), "to": self.window_to.isoformat()},
            "counts": self.counts,
            "days": [{"date": d, "items": v} for d, v in sorted(days.items())],
        }


# ---------------------------------------------------------------------------- database side


def user_today(s: Settings) -> date:
    return datetime.now(ZoneInfo(s.user_tz)).date()


def _run_days(db: Session, start: date, end: date) -> set[date]:
    rows = db.scalars(
        select(Run.started_at_local).where(
            Run.deleted_at.is_(None),
            Run.activity_type.in_(RUN_TYPES),
            Run.started_at_local >= datetime.combine(start, datetime.min.time()),
            Run.started_at_local < datetime.combine(end + timedelta(days=1), datetime.min.time()),
        )
    )
    return {r.date() for r in rows if r is not None}


def open_runs(db: Session, today: date, horizon_days: int = HORIZON_DAYS) -> dict[str, PlannedRun]:
    """Open planned runs from today on: status proposed/approved, a run kind and prescription,
    and no running activity on that day yet (reconcile may not have closed it yet)."""
    end = today + timedelta(days=horizon_days)
    done_days = _run_days(db, today, end)
    rows = db.scalars(
        select(PlannedSession)
        .where(
            PlannedSession.deleted_at.is_(None),
            PlannedSession.kind.in_(RUN_KINDS),
            PlannedSession.status.in_(OPEN_STATUSES),
            PlannedSession.day >= today,
            PlannedSession.day <= end,
        )
        .order_by(PlannedSession.day, PlannedSession.id)
    )
    out: dict[str, PlannedRun] = {}
    for p in rows:
        pr = p.prescription or {}
        session = pr.get("session") or {}
        if p.day in done_days or session.get("type") not in RUN_KINDS:
            continue
        out[p.id] = PlannedRun(p.id, p.day, p.kind, pr.get("title"), session)
    return out


# ---------------------------------------------------------------------------- workout JSON


def _num(x: float) -> str:
    return f"{x:g}"


def _km_label(km: float) -> str:
    return f"{round(km, 1):g} km"


def _pace_label(s_per_km: float) -> str:
    s = round(s_per_km)
    return f"{s // 60}:{s % 60:02d}"


def _rep_label(meters: float) -> str:
    return f"{meters / 1000:g}k" if meters >= 1000 and meters % 500 == 0 else f"{meters:g}m"


def short_label(run: PlannedRun) -> str:
    s = run.session
    t = s.get("type")
    if t == "intervals":
        st = s.get("structure") or {}
        work = st.get("work") or {}
        stype = st.get("session_type")
        reps, rep_m = int(work.get("reps") or 1), float(work.get("distance_m") or 0)
        if stype == "time_trial" or not st.get("recovery"):
            return f"TT {_km_label(rep_m * reps / 1000)}"
        return f"{INTERVAL_LABELS.get(stype, 'Int')} {reps}x{_rep_label(rep_m)}"
    km = float(s.get("distance_km") or 0)
    return f"{'Long' if t == 'long_run' else 'Easy'} {_km_label(km)}"


def workout_name(run: PlannedRun) -> str:
    """Short, dated and ASCII so it reads on the watch: `Thu 29.10 Easy 3 km`,
    `Fri 30.10 Thr 3x1k`, `Sun 01.11 Long 11 km`."""
    d = run.day
    name = f"{WEEKDAYS[d.weekday()]} {d.day:02d}.{d.month:02d} {short_label(run)}"
    return name[:NAME_MAX]


def _step_type(kind: str) -> dict[str, Any]:
    ids = {
        "warmup": (StepType.WARMUP, 1),
        "cooldown": (StepType.COOLDOWN, 2),
        "interval": (StepType.INTERVAL, 3),
        "recovery": (StepType.RECOVERY, 4),
    }
    sid, order = ids[kind]
    return {"stepTypeId": sid, "stepTypeKey": kind, "displayOrder": order}


def _end(kind: str) -> dict[str, Any]:
    ids = {
        "distance": (ConditionType.DISTANCE, 3),
        "time": (ConditionType.TIME, 2),
        "lap.button": (ConditionType.LAP_BUTTON, 1),
    }
    cid, order = ids[kind]
    return {
        "conditionTypeId": cid,
        "conditionTypeKey": kind,
        "displayOrder": order,
        "displayable": True,
    }


NO_TARGET = {
    "workoutTargetTypeId": TargetType.NO_TARGET,
    "workoutTargetTypeKey": "no.target",
    "displayOrder": 1,
}


def _hr_target(hr: dict[str, Any] | None, order: int = 1) -> tuple[dict, float, float] | None:
    """Custom HR range in bpm. Without a usable `low`, the ceiling alone (low = ceiling - 30)."""
    if not hr or hr.get("high") is None:
        return None
    high = float(hr["high"])
    low = float(hr["low"]) if hr.get("low") is not None else high - 30
    if low > high:
        low = high
    t = {
        "workoutTargetTypeId": TargetType.HEART_RATE_ZONE,
        "workoutTargetTypeKey": "heart.rate.zone",
        "displayOrder": order,
    }
    return t, low, high


def _pace_target(slow_s: float, fast_s: float, order: int = 1) -> tuple[dict, float, float]:
    """Pace range in m/s (Garmin: the slower pace is the lower limit)."""
    lo, hi = 1000.0 / max(slow_s, fast_s), 1000.0 / min(slow_s, fast_s)
    t = {
        "workoutTargetTypeId": TargetType.PACE_ZONE,
        "workoutTargetTypeKey": "pace.zone",
        "displayOrder": order,
    }
    return t, round(lo, 4), round(hi, 4)


class _Steps:
    def __init__(self) -> None:
        self.order = 0

    def step(
        self,
        kind: str,
        end: str,
        value: float | None,
        target: tuple[dict, float, float] | None = None,
        secondary: tuple[dict, float, float] | None = None,
        description: str | None = None,
    ) -> ExecutableStep:
        self.order += 1
        kw: dict[str, Any] = {
            "stepOrder": self.order,
            "stepType": _step_type(kind),
            "endCondition": _end(end),
            "endConditionValue": float(value) if value is not None else None,
            "targetType": NO_TARGET,
            "description": description,
        }
        if target:
            kw["targetType"], kw["targetValueOne"], kw["targetValueTwo"] = target
        if secondary:
            t, a, b = secondary
            kw["secondaryTargetType"] = {**t, "displayOrder": 2}
            kw["secondaryTargetValueOne"], kw["secondaryTargetValueTwo"] = a, b
        return ExecutableStep(**kw)

    def repeat(self, n: int, build: Callable[[], list[ExecutableStep]]) -> RepeatGroup:
        self.order += 1
        group_order = self.order
        children = build()
        return RepeatGroup(
            stepOrder=group_order,
            stepType={"stepTypeId": StepType.REPEAT, "stepTypeKey": "repeat", "displayOrder": 6},
            numberOfIterations=n,
            workoutSteps=children,
            endCondition={
                "conditionTypeId": ConditionType.ITERATIONS,
                "conditionTypeKey": "iterations",
                "displayOrder": 7,
                "displayable": False,
            },
            endConditionValue=float(n),
            skipLastRestStep=True,
        )


def _easy_pace_s(session: dict[str, Any]) -> float:
    p = session.get("pace") or {}
    lo, hi = p.get("min_s_per_km"), p.get("max_s_per_km")
    if lo and hi:
        return (float(lo) + float(hi)) / 2
    return 390.0


def _easy_steps(run: PlannedRun, steps: _Steps) -> tuple[list, int, str]:
    s = run.session
    km = float(s.get("distance_km") or 0)
    hr = s.get("hr") or {}
    target = _hr_target(hr)
    pace_s = _easy_pace_s(s)
    out: list = [
        steps.step(
            "interval",
            "distance",
            km * 1000,
            target,
            description=f"easy, HR below {hr.get('high')}" if hr.get("high") else "easy",
        )
    ]
    seconds = km * pace_s
    summary = f"{'Long' if s.get('type') == 'long_run' else 'Easy'} {_km_label(km)}"
    if hr.get("high"):
        summary += f", HR {_num(hr.get('low') or 0)}-{_num(hr['high'])}"
    strides = (s.get("structure") or {}).get("strides")
    if strides and int(strides.get("reps") or 0) > 0:
        reps, dur, rec = (
            int(strides["reps"]),
            float(strides.get("duration_s") or 20),
            float(strides.get("recovery_s") or 60),
        )

        def body() -> list[ExecutableStep]:
            return [
                steps.step("interval", "time", dur, description="relaxed fast stride"),
                steps.step("recovery", "time", rec, description="easy jog / walk"),
            ]

        out.append(steps.repeat(reps, body))
        seconds += reps * (dur + rec)
        summary += f" + {reps}x{_num(dur)}s strides"
    return out, round(seconds), summary


def _interval_steps(run: PlannedRun, steps: _Steps) -> tuple[list, int, str]:
    st = run.session.get("structure") or {}
    wu, work, rec, cd = (
        st.get("warmup"),
        st.get("work") or {},
        st.get("recovery"),
        st.get("cooldown"),
    )
    easy_s = 390.0
    out: list = []
    seconds = 0.0
    if wu and wu.get("distance_km"):
        out.append(
            steps.step(
                "warmup", "distance", float(wu["distance_km"]) * 1000, _hr_target(wu.get("hr"))
            )
        )
        seconds += float(wu["distance_km"]) * easy_s
    reps = max(int(work.get("reps") or 1), 1)
    rep_m = float(work.get("distance_m") or 0)
    pace = work.get("pace_s_per_km")
    rng = work.get("pace_range_s_per_km") or ([pace, pace] if pace else None)
    pace_t = _pace_target(float(rng[1]), float(rng[0])) if rng else None
    hr_t = _hr_target(work.get("hr"), order=2)
    rep_s = float(work.get("duration_s") or (rep_m / 1000 * (pace or 300)))

    def work_step() -> ExecutableStep:
        if rep_m > 0:
            return steps.step(
                "interval",
                "distance",
                rep_m,
                pace_t or hr_t,
                hr_t if pace_t else None,
                description=f"@ {work.get('pace_label') or ''} /km".strip(),
            )
        return steps.step("interval", "time", rep_s, pace_t or hr_t, hr_t if pace_t else None)

    if rec and reps > 1:
        rec_s, rec_m = rec.get("duration_s"), rec.get("distance_m")

        def body() -> list[ExecutableStep]:
            first = work_step()
            if rec_s:
                second = steps.step("recovery", "time", float(rec_s), description="jog")
            elif rec_m:
                second = steps.step("recovery", "distance", float(rec_m), description="jog")
            else:
                second = steps.step("recovery", "lap.button", None, description="jog")
            return [first, second]

        out.append(steps.repeat(reps, body))
        seconds += reps * rep_s + (reps - 1) * float(rec_s or (rec_m or 0) / 1000 * easy_s)
    else:
        for _ in range(reps):
            out.append(work_step())
        seconds += reps * rep_s
    if cd and cd.get("distance_km"):
        out.append(
            steps.step(
                "cooldown", "distance", float(cd["distance_km"]) * 1000, _hr_target(cd.get("hr"))
            )
        )
        seconds += float(cd["distance_km"]) * easy_s
    label = short_label(run)
    summary = label
    if work.get("pace_label"):
        summary += f" @ {work['pace_label']}/km"
    if rec and reps > 1:
        summary += (
            f", {_num(float(rec['duration_s']))}s jog"
            if rec.get("duration_s")
            else f", {_num(float(rec.get('distance_m') or 0))}m jog"
        )
    if wu and cd:
        summary += (
            f"; WU {_km_label(float(wu.get('distance_km') or 0))}"
            f" / CD {_km_label(float(cd.get('distance_km') or 0))}"
        )
    return out, round(seconds), summary


def content_hash(name: str, day: date, workout: RunningWorkout) -> str:
    body = workout.to_dict()
    body.pop("description", None)
    blob = json.dumps(
        {"v": BUILDER_VERSION, "name": name, "day": day.isoformat(), "workout": body},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(blob.encode()).hexdigest()[:12]


def marker(pid: str, day: date, h: str) -> str:
    return f"[FitnessBuddy ps={pid} d={day.isoformat()} h={h}]"


def build_workout(run: PlannedRun) -> WorkoutSpec:
    """The Garmin running workout of one planned run (pure)."""
    steps = _Steps()
    if run.session.get("type") == "intervals":
        body, seconds, summary = _interval_steps(run, steps)
    else:
        body, seconds, summary = _easy_steps(run, steps)
    name = workout_name(run)
    workout = RunningWorkout(
        workoutName=name,
        estimatedDurationInSecs=seconds,
        workoutSegments=[
            WorkoutSegment(
                segmentOrder=1,
                sportType={"sportTypeId": 1, "sportTypeKey": "running", "displayOrder": 1},
                workoutSteps=body,
            )
        ],
    )
    h = content_hash(name, run.day, workout)
    note = f"{summary}. FitnessBuddy plan, synced automatically (edits are overwritten). "
    room = DESCRIPTION_MAX - len(marker(run.planned_session_id, run.day, h))
    workout.description = note[:room] + marker(run.planned_session_id, run.day, h)
    return WorkoutSpec(run, name, h, workout)


def fingerprint(specs: list[WorkoutSpec]) -> str:
    keys = sorted((w.run.planned_session_id, w.run.day.isoformat(), w.content_hash) for w in specs)
    return hashlib.sha256(json.dumps(keys).encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------- Garmin side


class _Remote:
    """Paced, error-translated calls on a logged-in python-garminconnect client."""

    def __init__(self, api: Any, pace_s: float, sleep: Callable[[float], None]) -> None:
        self.api, self.pace_s, self.sleep = api, pace_s, sleep

    def __call__(self, method: str, *args: Any, **kwargs: Any) -> Any:
        self.sleep(self.pace_s)
        try:
            return getattr(self.api, method)(*args, **kwargs)
        except Exception as exc:
            raise translate_error(exc) from None


def parse_marker(description: str | None) -> tuple[str, date, str] | None:
    m = MARKER_RE.search(description or "")
    if not m:
        return None
    try:
        return m["pid"].lower(), date.fromisoformat(m["day"]), m["hash"]
    except ValueError:
        return None


def list_ours(remote: _Remote, *, page: int = 100, max_pages: int = 10) -> list[RemoteWorkout]:
    """Our workouts in the Garmin library (marker in the description). A list item without a
    description whose name looks like ours is fetched in full to check its marker."""
    out: list[RemoteWorkout] = []
    for i in range(max_pages):
        items = remote("get_workouts", i * page, page) or []
        for it in items:
            if not isinstance(it, dict) or it.get("workoutId") is None:
                continue
            desc = it.get("description")
            if desc is None and NAME_RE.match(str(it.get("workoutName") or "")):
                try:
                    desc = (remote("get_workout_by_id", it["workoutId"]) or {}).get("description")
                except NotFoundError:
                    continue
            parsed = parse_marker(desc)
            if parsed is None:
                continue  # not ours: never touched
            pid, day, h = parsed
            out.append(
                RemoteWorkout(int(it["workoutId"]), str(it.get("workoutName") or ""), pid, day, h)
            )
        if len(items) < page:
            break
    return out


def _schedule_entries(payload: Any) -> list[dict[str, Any]] | None:
    """Entries of `workoutScheduleByPeriod`; None when the shape is not understood."""
    if isinstance(payload, list):
        return [e for e in payload if isinstance(e, dict)]
    if isinstance(payload, dict):
        for key in ("workoutScheduleList", "scheduledWorkouts", "calendarItems", "items"):
            if isinstance(payload.get(key), list):
                return [e for e in payload[key] if isinstance(e, dict)]
    return None


def _entry_ids(e: dict[str, Any]) -> tuple[int | None, int | None, str | None]:
    wid = e.get("workoutId")
    if wid is None and isinstance(e.get("workout"), dict):
        wid = e["workout"].get("workoutId")
    sid = e.get("workoutScheduleId") or e.get("scheduleId") or e.get("id")
    day = e.get("calendarDate") or e.get("date") or e.get("scheduledDate")
    try:
        return (int(wid) if wid is not None else None, int(sid) if sid is not None else None, day)
    except (TypeError, ValueError):
        return None, None, day


def attach_schedules(remote: _Remote, ours: list[RemoteWorkout], start: date, end: date) -> bool:
    """Fill `schedules` of our workouts. Returns False when the listing failed or its shape was
    not understood (then nothing is (re)scheduled on the basis of it)."""
    if not ours:
        return True
    try:
        payload = remote("get_scheduled_workouts_by_period", start.isoformat(), end.isoformat())
    except GarminError as exc:
        log.warning("Garmin schedule listing failed: %s", exc)
        return False
    entries = _schedule_entries(payload)
    if entries is None:
        log.warning("Garmin schedule listing: unexpected response shape")
        return False
    by_id = {w.workout_id: w for w in ours}
    parsed_any = False
    for e in entries:
        wid, sid, day = _entry_ids(e)
        if wid is None or sid is None:
            continue
        parsed_any = True
        if wid in by_id:
            by_id[wid].schedules.append((sid, str(day or "")[:10]))
    return parsed_any or not entries


def _delete(remote: _Remote, w: RemoteWorkout) -> None:
    for sid, _ in w.schedules:
        with contextlib.suppress(NotFoundError):
            remote("unschedule_workout", sid)
    with contextlib.suppress(NotFoundError):  # already gone
        remote("delete_workout", w.workout_id)


def _upload(remote: _Remote, spec: WorkoutSpec) -> int:
    res = remote("upload_running_workout", spec.workout) or {}
    wid = res.get("workoutId") if isinstance(res, dict) else None
    if wid is None:
        raise GarminError("python-garminconnect: upload returned no workoutId")
    remote("schedule_workout", int(wid), spec.run.day.isoformat())
    return int(wid)


# ---------------------------------------------------------------------------- the push


def desired_workouts(
    candidates: dict[str, PlannedRun],
    window_from: date,
    window_to: date,
    keep: set[str] = frozenset(),  # type: ignore[assignment]
    extra_day: date | None = None,
) -> list[WorkoutSpec]:
    out = []
    for pid, run in candidates.items():
        if window_from <= run.day <= window_to or pid in keep or run.day == extra_day:
            out.append(build_workout(run))
    return sorted(out, key=lambda w: (w.run.day, w.name))


def _deletion_reason(w: RemoteWorkout, today: date) -> str:
    return "past" if w.day < today else "run done or session no longer planned"


def reconcile_remote(
    remote: _Remote,
    desired: list[WorkoutSpec],
    ours: list[RemoteWorkout],
    *,
    today: date,
    schedules_known: bool,
    report: PushReport,
) -> None:
    by_pid: dict[str, list[RemoteWorkout]] = {}
    for w in ours:
        by_pid.setdefault(w.planned_session_id, []).append(w)
    # 1. upload / replace / keep what we want on the watch
    for spec in desired:
        pid, day = spec.run.planned_session_id, spec.run.day.isoformat()
        have = by_pid.pop(pid, [])
        same = [w for w in have if w.content_hash == spec.content_hash and w.day == spec.run.day]
        keep, stale = (same[0], [w for w in have if w is not same[0]]) if same else (None, have)
        try:
            if keep is not None:
                scheduled = any(d == day for _, d in keep.schedules)
                if schedules_known and not scheduled:
                    remote("schedule_workout", keep.workout_id, day)
                    report.items.append(
                        PushItem(
                            day,
                            "scheduled",
                            spec.name,
                            pid,
                            keep.workout_id,
                            "was not on the calendar",
                        )
                    )
                else:
                    report.items.append(PushItem(day, "unchanged", spec.name, pid, keep.workout_id))
            else:
                wid = _upload(remote, spec)
                action = "updated" if stale else "uploaded"
                reason = "plan changed" if stale else None
                report.items.append(PushItem(day, action, spec.name, pid, wid, reason))
        except GarminError as exc:
            log.warning("Garmin workout push failed for %s: %s", day, exc)
            report.items.append(PushItem(day, "failed", spec.name, pid, None, str(exc)))
            continue  # keep the old workout rather than leaving the day empty
        for old in stale:
            try:
                _delete(remote, old)
            except GarminError as exc:
                log.warning("Garmin: deleting a replaced workout failed: %s", exc)
    # 2. delete ours that are past, done or no longer planned
    for pid, rest in by_pid.items():
        for w in rest:
            reason = _deletion_reason(w, today)
            try:
                _delete(remote, w)
                report.items.append(
                    PushItem(w.day.isoformat(), "deleted", w.name, pid, w.workout_id, reason)
                )
            except GarminError as exc:
                log.warning("Garmin: deleting an old workout failed: %s", exc)
                report.items.append(
                    PushItem(w.day.isoformat(), "failed", w.name, pid, w.workout_id, str(exc))
                )


ClientProvider = Callable[[Settings], Any]


def _default_client(s: Settings) -> Any:
    if not (s.garmin_email and s.garmin_password):
        raise SourceUnavailable("GARMIN_EMAIL / GARMIN_PASSWORD are not set")
    return shared_login(
        s.garmin_email, s.garmin_password.get_secret_value(), s.garmin_connect_token_dir
    ).client()


client_provider: ClientProvider = _default_client  # tests replace this with a fake
_push_lock = threading.Lock()
_last_fingerprint: str | None = None


def push_workouts(
    db: Session,
    settings: Settings | None = None,
    *,
    today: date | None = None,
    extra_day: date | None = None,
    dry_run: bool = False,
    sleep: Callable[[float], None] = time.sleep,
    lock_timeout_s: float = 120.0,
) -> PushReport:
    """Bring our Garmin workouts in line with the open planned runs (see module docstring).

    `dry_run` builds the workouts and lists what would be uploaded, without contacting Garmin
    (no login). Raises `SourceUnavailable` (no credentials / login failed) or `GarminError`
    only for the listing step; per-workout failures are reported as `failed` items."""
    global _last_fingerprint
    s = settings or get_settings()
    today = today or user_today(s)
    window_to = today + timedelta(days=max(s.garmin_push_days, 0))
    candidates = open_runs(db, today, max(HORIZON_DAYS, s.garmin_push_days))
    db.rollback()  # end the read transaction before the (slow) Garmin calls
    report = PushReport(dry_run, today, window_to)
    if dry_run:
        report.remote_checked = False
        report.workouts = desired_workouts(candidates, today, window_to, extra_day=extra_day)
        for spec in report.workouts:
            report.items.append(
                PushItem(
                    spec.run.day.isoformat(),
                    "uploaded",
                    spec.name,
                    spec.run.planned_session_id,
                    None,
                    "dry run: Garmin not contacted",
                )
            )
        return report
    if not _push_lock.acquire(timeout=lock_timeout_s):
        raise GarminError("another workout push is still running")
    try:
        remote = _Remote(client_provider(s), s.garmin_request_interval_s, sleep)
        ours = list_ours(remote)
        keep = {w.planned_session_id for w in ours if w.planned_session_id in candidates}
        report.workouts = desired_workouts(candidates, today, window_to, keep, extra_day)
        last_day = max([window_to, *(w.day for w in ours), *(w.run.day for w in report.workouts)])
        schedules_known = attach_schedules(
            remote,
            ours,
            min([today - timedelta(days=PAST_LOOKBACK_DAYS), *(w.day for w in ours)]),
            last_day,
        )
        reconcile_remote(
            remote,
            report.workouts,
            ours,
            today=today,
            schedules_known=schedules_known,
            report=report,
        )
        if not report.counts["failed"]:  # what push_if_changed compares against
            _last_fingerprint = fingerprint(desired_workouts(candidates, today, window_to))
        log.info("Garmin workout push: %s", report.counts)
        return report
    finally:
        _push_lock.release()


def _enabled(s: Settings) -> bool:
    return bool(s.garmin_push_workouts and s.garmin_email and s.garmin_password)


def push_job() -> None:
    """Scheduled push (evening + early morning). Never raises."""
    from apps.fitness.db.session import session_scope

    s = get_settings()
    if not _enabled(s):
        return
    try:
        with session_scope() as db:
            push_workouts(db, s)
    except Exception as exc:  # never break the scheduler; messages carry no secrets
        log.warning("scheduled Garmin workout push failed: %s", type(exc).__name__ + f": {exc}")


def push_if_changed(reason: str, settings: Settings | None = None) -> PushReport | None:
    """Push only when the wanted workouts differ from the last successful push of this process
    (an engine re-run that left the runs alone costs no Garmin request). Never raises."""
    from apps.fitness.db.session import session_scope

    s = settings or get_settings()
    try:
        today = user_today(s)
        with session_scope() as db:
            cands = open_runs(db, today, s.garmin_push_days)
            wanted = desired_workouts(cands, today, today + timedelta(days=s.garmin_push_days))
            if _last_fingerprint is not None and fingerprint(wanted) == _last_fingerprint:
                return None
            report = push_workouts(db, s, today=today)
        log.info("Garmin workout push after %s: %s", reason, report.counts)
        return report
    except Exception as exc:
        log.warning(
            "Garmin workout push after %s failed: %s", reason, type(exc).__name__ + f": {exc}"
        )
        return None


def push_soon(reason: str) -> None:
    """Hook after an engine re-run: push changed runs in a background thread. Only in the
    server process (SCHEDULER_ENABLED) so local runs and tests never talk to Garmin."""
    s = get_settings()
    if not (s.scheduler_enabled and s.garmin_push_after_engine and _enabled(s)):
        return
    threading.Thread(
        target=push_if_changed, args=(reason, s), name="garmin-workout-push", daemon=True
    ).start()


__all__ = [
    "PlannedRun",
    "PushItem",
    "PushReport",
    "WorkoutSpec",
    "build_workout",
    "open_runs",
    "push_if_changed",
    "push_job",
    "push_soon",
    "push_workouts",
    "workout_name",
]
