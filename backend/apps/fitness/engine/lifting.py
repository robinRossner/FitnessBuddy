"""Lifting progression for one exercise variant. Pure functions over plain data.

Spec: docs/research/lifting-progression.md (sections 3-5) with the decisions in
docs/spec/decisions.md ("Progression engine v2"). The user trains every working set to 0-1 RIR,
so the engine never prescribes RPE/RIR: **reps at a given load** are the performance signal.

Per exposure (one session's working sets of the variant):

* `S` = reps across the working sets at the main (top) load; comparisons use the first k sets
  that both exposures have (a yellow day with one set fewer still compares);
* progress event = load PR (with every set >= the range bottom), rep PR at the same load, or
  best-set e1RM up >= `e1rm_progress_pct`;
* censored = a MISS that does not count (hits always count): yellow / red readiness, lower-body
  lift within 24 h after intervals or a long run, the first sessions of a variant / after a
  break / after a gym change, deload weeks. The reason travels into decisions_log.

Next target, in this order:

1. deload window (multi-lift stall, fatigue index, red streak): sets halved, loads kept;
2. retest of the old load after a multi-lift deload;
3. return after a break (config `strength.break_tiers`);
4. a pending stall-ladder reset;
5. stall (no progress event in N clean exposures, >= `stall_min_days`): the per-lift ladder
   -small % -> advisor (rep range / variation) -> -large % (the planner turns >= 3 stalls into
   one soft deload + retest instead);
6. first exposure at a new load far below the floor -> back to the previous rung;
7. a real drop (>= `big_miss_reps` fewer reps, or two clean drops in a row) -> one rung down;
   a dip of 1-2 reps is noise: same target again;
8. two-strike gate (every set >= top-1 on two consecutive clean exposures, or every set at the
   top once) -> next rung if Epley predicts >= `rung_min_reps` there, else the rep ceiling is
   raised (<= `rep_ceiling_cap`) until it does;
9. otherwise +1 rep per set (capped at the effective top) on the better of the last two.

The first prescription for a variant without history has no weight (the app asks for one).
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from apps.fitness.engine.config import BreakTier, EngineConfig
from apps.fitness.engine.increments import VariantLike, next_load, previous_load, round_down_load

_EPS = 1e-9

# progression_state values of a plan item
PROGRESSING, GRINDING, STALLED = "progressing", "grinding", "stalled"
DELOAD, RETEST, REBUILD = "deload", "retest", "rebuild"
BASELINE = "baseline"  # a variant without history (new machine / new gym): find the first load
STATES = (PROGRESSING, GRINDING, STALLED, DELOAD, RETEST, REBUILD, BASELINE)

# stall-ladder steps
LADDER_RESET_SMALL, LADDER_ADVISOR, LADDER_RESET_LARGE = 1, 2, 3


@dataclass(frozen=True)
class SetResult:
    weight: float | None
    reps: int | None
    duration_s: float | None = None


@dataclass(frozen=True)
class Exposure:
    """One session's working sets of one variant (warm-ups excluded), oldest first in history."""

    day: date
    sets: tuple[SetResult, ...]
    best_e1rm: float | None = None
    gym_id: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "date": self.day.isoformat(),
            "sets": [
                {"weight": s.weight, "reps": s.reps, "duration_s": s.duration_s} for s in self.sets
            ],
            "best_e1rm": self.best_e1rm,
        }


@dataclass(frozen=True)
class LiftSpec:
    variant: VariantLike
    rep_range: tuple[int, int]
    n_sets: int
    increment_kg: float | None = None  # per-exercise override of the load step
    timed: bool = False
    per_week: int = 1  # program days per week with this lift (2 for upper, 1 for lower)
    lower_body: bool = False
    bodyweight_kg: float | None = None  # for bodyweight / assistance loads in the Epley maths


@dataclass(frozen=True)
class LadderEvent:
    """One step of the per-lift stall ladder (decisions_log `output.ladder`)."""

    day: date  # first planned day the step applies to
    step: int  # 1 = -small %, 2 = advisor (rep range / variation), 3 = -large %
    load: float | None  # load after the step (the held load for step 2)
    peak_e1rm: float | None = None  # best e1RM before the step: beating it ends the episode
    from_load: float | None = None


@dataclass(frozen=True)
class Retest:
    """Retest after a soft deload: the old load and the reps to beat."""

    load: float
    reps: tuple[int, ...]
    best_e1rm: float | None = None


@dataclass(frozen=True)
class DeloadWindow:
    start: date
    until: date
    reason: str  # multi_lift_stall | fatigue_index | red_streak | legacy_reset
    retest: dict[int, Retest] = field(default_factory=dict)  # variant id -> retest
    deload: bool = True  # False: only the retest (a converted single reset, or a cancelled deload)
    cancelled: bool = False  # the user cancelled the deload: the retest starts without it

    def covers(self, d: date) -> bool:
        return self.deload and self.start <= d <= self.until


@dataclass
class LiftEnv:
    """Everything outside the lift's own history that its prescription depends on."""

    variant_id: int | None = None
    readiness: dict[date, str] = field(default_factory=dict)  # known colours by day
    hard_run_days: set[date] = field(default_factory=set)  # intervals / long runs
    windows: list[DeloadWindow] = field(default_factory=list)
    ladder: list[LadderEvent] = field(default_factory=list)
    allow_ladder: bool = True  # False while the planner handles a multi-lift stall
    # No history here yet: the same exercise at another gym (a hint, machines differ): its last
    # top set. Filled by the planner's data loader; the targets stay empty either way.
    previous_gym: str | None = None
    previous_weight_kg: float | None = None
    previous_reps: int | None = None


@dataclass
class SetTarget:
    index: int
    weight: float | None
    reps: int | None
    set_type: str = "working"
    duration_s: float | None = None

    def as_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "index": self.index,
            "set_type": self.set_type,
            "target_weight": self.weight,
            "target_reps": self.reps,
        }
        if self.duration_s is not None:
            d["target_duration_s"] = self.duration_s
        return d


@dataclass
class LiftPrescription:
    sets: list[SetTarget]
    rule: str
    reason: str
    previous: Exposure | None = None
    flags: list[str] = field(default_factory=list)
    stall: bool = False
    inputs: dict[str, Any] = field(default_factory=dict)
    state: str = GRINDING
    note: str = ""
    effective_rep_range: tuple[int, int] | None = None
    ladder: LadderEvent | None = None  # a NEW ladder step decided by this prescription
    retest: bool = False
    hint_weight_kg: float | None = None  # baseline only: last top set at another gym

    @property
    def load(self) -> float | None:
        return self.sets[0].weight if self.sets else None


# --------------------------------------------------------------------------- maths


def e1rm(load: float, reps: int) -> float:
    return load * (1 + reps / 30)


def predicted_reps(e1rm_value: float, load: float) -> float:
    """Epley solved for reps: 30 x (1RM / load - 1)."""
    return 30 * (e1rm_value / load - 1) if load > 0 else 0.0


def ceiling_needed(load_eff: float, next_eff: float, rung_min: int) -> int:
    """Reps at `load_eff` that make Epley predict >= `rung_min` reps at `next_eff`."""
    if load_eff <= 0:
        return rung_min
    return math.ceil(30 * ((1 + rung_min / 30) * next_eff / load_eff - 1) - 1e-6)


def effective_load(spec: LiftSpec, w: float) -> float:
    """The load the muscles move: + bodyweight for bodyweight lifts, - assistance."""
    bw = spec.bodyweight_kg or 82.0
    if spec.variant.load_convention == "assistance":
        return max(1.0, bw - w)
    if spec.variant.equipment_type == "bodyweight":
        return bw + w
    return w


def _working(exposure: Exposure) -> list[SetResult]:
    return [s for s in exposure.sets if s.reps is not None or s.duration_s is not None]


def main_load(exposure: Exposure) -> float:
    return max((s.weight or 0.0) for s in _working(exposure)) if _working(exposure) else 0.0


def main_reps(exposure: Exposure) -> tuple[int, ...]:
    """Reps of the working sets at the main load (in order)."""
    w = main_load(exposure)
    return tuple(int(s.reps or 0) for s in _working(exposure) if abs((s.weight or 0.0) - w) < 1e-6)


def _s_k(a: Sequence[int], b: Sequence[int]) -> tuple[int, int]:
    """Totals of the first k sets both have."""
    k = min(len(a), len(b))
    return sum(a[:k]), sum(b[:k])


def _step(spec: LiftSpec, cfg: EngineConfig, current: float, direction: int) -> float:
    if spec.increment_kg:
        return max(0.0, round(current + direction * spec.increment_kg, 3))
    if direction > 0:
        return next_load(spec.variant, current, cfg)
    return previous_load(spec.variant, current, cfg)


def reduce_load(spec: LiftSpec, cfg: EngineConfig, load: float, pct: float) -> float:
    """`load` made easier by `pct` %, rounded to an available load (assistance: more help)."""
    if spec.variant.load_convention == "assistance":
        target = load * (1 + pct / 100) if load > 0 else load + 5
        return round_down_load(spec.variant, load, target, cfg)
    target = load * (1 - pct / 100)
    if spec.increment_kg and spec.increment_kg > 0:
        k = math.ceil((load - target) / spec.increment_kg - _EPS)
        return max(0.0, round(load - k * spec.increment_kg, 3))
    return round_down_load(spec.variant, load, target, cfg)


def deload_set_count(n: int, cfg: EngineConfig) -> int:
    d = cfg.strength.deload
    raw = n * (1 - d.sets_pct / 100)
    if d.sets_rounding == "up":
        return max(1, math.ceil(raw - 1e-9))
    return max(1, round(raw))


def drop_sets(sets: list[SetTarget], n: int = 1) -> list[SetTarget]:
    """Yellow readiness: same effort, `n` fewer working sets (min 1)."""
    keep = max(1, len(sets) - n)
    return [
        SetTarget(i + 1, s.weight, s.reps, s.set_type, s.duration_s)
        for i, s in enumerate(sets[:keep])
    ]


def _fmt_reps(reps: Sequence[int]) -> str:
    return "/".join(str(r) for r in reps)


def detect_stall(e1rms: Sequence[float | None], n: int) -> bool:
    """Dashboard indicator (stats API): no new best e1RM over the last `n` exposures. The
    engine itself uses `analyze` (progress events, censoring, minimum days)."""
    vals = [v for v in e1rms if v is not None]
    if n < 1 or len(vals) < n + 1:
        return False
    before, window = vals[:-n], vals[-n:]
    return max(window) <= max(before) + _EPS


# --------------------------------------------------------------------------- analysis


@dataclass
class ExpoEval:
    expo: Exposure
    load: float
    reps: tuple[int, ...]
    e1rm: float | None
    censored: str | None = None
    event: str | None = None  # load_pr | rep_pr | e1rm_pr
    drop: int = 0  # reps fewer than the previous comparable exposure (first k sets)
    miss: bool = False  # fewer reps than the previous comparable exposure

    @property
    def total(self) -> int:
        return sum(self.reps)

    @property
    def counts(self) -> bool:
        """Clean, or a hit on a censored day (hits always count)."""
        return self.censored is None or self.event is not None or not self.miss


@dataclass
class LiftAnalysis:
    evals: list[ExpoEval]
    counter: int = 0  # clean exposures without a progress event since the last event
    since: date | None = None  # day of the last progress event (or the start of the window)
    n_required: int = 3
    stalled: bool = False
    ladder_pos: int = 0  # ladder steps taken in the current episode
    peak_e1rm: float | None = None
    baseline: date | None = None  # exposures before this day do not count for a stall
    retest_failed: bool = False

    @property
    def last(self) -> ExpoEval | None:
        return self.evals[-1] if self.evals else None

    def censored(self, limit: int = 6) -> list[list[str]]:
        return [
            [e.expo.day.isoformat(), e.censored]
            for e in self.evals[-limit:]
            if e.censored and e.miss
        ]


def _censor_reason(
    i: int, ev: list[Exposure], spec: LiftSpec, cfg: EngineConfig, ctx: LiftEnv
) -> str | None:
    c = cfg.strength.censor
    e = ev[i]
    d = e.day
    if any(w.covers(d) for w in ctx.windows):
        return "deload week"
    if i < c.calibration_sessions:
        return "first sessions of this variant (calibration)"
    for back in range(1, c.calibration_sessions + 1):
        j = i - back + 1  # the exposure that started the calibration (this one or an earlier)
        if j < 1:
            break
        gap = (ev[j].day - ev[j - 1].day).days
        if gap >= c.break_days:
            return f"first sessions after a {gap}-day break"
        gyms = (ev[j].gym_id, ev[j - 1].gym_id)
        if None not in gyms and gyms[0] != gyms[1]:
            return "first sessions after a gym change"
    color = ctx.readiness.get(d)
    if color in c.readiness:
        return f"readiness {color}"
    if spec.lower_body:
        days = max(1, math.ceil(c.hard_run_hours / 24))
        if any((d - timedelta(days=k)) in ctx.hard_run_days for k in range(0, days + 1)):
            return "within 24 h after intervals / a long run"
    return None


def analyze(
    spec: LiftSpec, history: Sequence[Exposure], cfg: EngineConfig, ctx: LiftEnv | None = None
) -> LiftAnalysis:
    ctx = ctx or LiftEnv()
    st = cfg.strength
    lo = spec.rep_range[0]
    hist = [e for e in history if _working(e)]
    evals: list[ExpoEval] = []
    best_load = None
    best_e1 = None
    for i, e in enumerate(hist):
        load, reps = main_load(e), main_reps(e)
        e1 = e.best_e1rm
        if e1 is None and reps:
            e1 = e1rm(effective_load(spec, load), max(reps))
        ee = ExpoEval(e, load, reps, e1)
        ee.censored = _censor_reason(i, hist, spec, cfg, ctx)
        if i > 0:
            heavier = best_load is not None and _harder(spec, load, best_load)
            same = [x for x in evals if abs(x.load - load) < 1e-6]
            if heavier and all(r >= lo for r in reps) and reps:
                ee.event = "load_pr"
            elif same:
                ref = max(same, key=lambda x: x.total)
                cur, old = _s_k(reps, ref.reps)
                if cur > old:
                    ee.event = "rep_pr"
            gain = 1 + st.e1rm_progress_pct / 100
            if (
                ee.event is None
                and e1 is not None
                and best_e1 is not None
                and e1 >= best_e1 * gain - 1e-9
            ):
                ee.event = "e1rm_pr"
            prev = next((x for x in reversed(evals) if abs(x.load - load) < 1e-6), None)
            if prev is not None:
                cur, old = _s_k(reps, prev.reps)
                ee.drop = old - cur
                ee.miss = cur < old
        if best_load is None or _harder(spec, load, best_load):
            best_load = load
        if e1 is not None:
            best_e1 = e1 if best_e1 is None else max(best_e1, e1)
        evals.append(ee)

    an = LiftAnalysis(evals)
    an.n_required = st.stall_exposures_2x if spec.per_week >= 2 else st.stall_exposures
    # stall ladder episode
    pos = 0
    peak = None
    events = sorted(ctx.ladder, key=lambda x: x.day)
    for k, ev in enumerate(events):
        if k > 0 and _recovered(evals, events[k - 1], ev.day):
            pos = 0
        pos += 1
        peak = ev.peak_e1rm if ev.peak_e1rm is not None else peak
    if events and _recovered(evals, events[-1], None):
        pos = 0
    an.ladder_pos = pos
    an.peak_e1rm = peak
    an.baseline = events[-1].day if events else None
    # retests restart the count as well
    for w in ctx.windows:
        if (
            ctx.variant_id in w.retest
            and (an.baseline is None or w.start > an.baseline)
            and any(x.expo.day > w.until for x in evals)
        ):
            an.baseline = w.start
    # a retest after a soft deload that brought no progress event: the lift is still stalled
    retest_failed = False
    for w in ctx.windows:
        if ctx.variant_id not in w.retest:
            continue
        post = [x for x in evals if x.expo.day > w.until]
        if post and post[0].event is None and not any(e.day > w.until for e in events):
            retest_failed = True
    # stall counter
    counter, since = 0, None
    for x in evals:
        if an.baseline is not None and x.expo.day < an.baseline:
            continue
        if since is None:
            since = x.expo.day
        if x.event is not None:
            counter, since = 0, x.expo.day
        elif x.censored is None:
            counter += 1
    an.counter, an.since = counter, since
    if evals and since is not None:
        elapsed = (evals[-1].expo.day - since).days
        an.stalled = counter >= an.n_required and elapsed >= st.stall_min_days
    if retest_failed:
        an.stalled, an.retest_failed = True, True
    return an


def _harder(spec: LiftSpec, a: float, b: float) -> bool:
    """`a` is a heavier (harder) load than `b`; assistance: less help is harder."""
    if spec.variant.load_convention == "assistance":
        return a < b - 1e-6
    return a > b + 1e-6


def _recovered(evals: list[ExpoEval], ev: LadderEvent, until: date | None) -> bool:
    if ev.peak_e1rm is None:
        return False
    return any(
        x.expo.day >= ev.day
        and (until is None or x.expo.day < until)
        and x.e1rm is not None
        and x.e1rm > ev.peak_e1rm + 1e-9
        for x in evals
    )


def fatigue_share(an: LiftAnalysis, week_start: date) -> tuple[int, int]:
    """(misses or load drops, clean exposures) of this lift in the week starting `week_start`."""
    end = week_start + timedelta(days=7)
    clean = [x for x in an.evals if week_start <= x.expo.day < end and x.censored is None]
    bad = 0
    for x in clean:
        i = an.evals.index(x)
        prev = an.evals[i - 1] if i > 0 else None
        if x.miss or (prev is not None and x.load < prev.load - 1e-6 and not x.event):
            bad += 1
    return bad, len(clean)


# --------------------------------------------------------------------------- targets


def _targets(n: int, load: float | None, reps: Sequence[int], hi: int, plus: int = 1):
    src = list(reps) or [0]
    out = []
    for i in range(n):
        r = src[i] if i < len(src) else src[-1]
        out.append(SetTarget(i + 1, load, min(r + plus, hi)))
    return out


def _predicted_targets(
    spec: LiftSpec, n: int, old: float, new: float, reps: Sequence[int], lo: int, hi: int
) -> list[SetTarget]:
    """Per-set targets at `new` predicted by Epley from `reps` at `old` (clamped lo..hi)."""
    o, w = effective_load(spec, old), effective_load(spec, new)
    src = list(reps) or [lo]
    out = []
    for i in range(n):
        r = src[i] if i < len(src) else src[-1]
        p = math.floor(predicted_reps(e1rm(o, r), w) + 1e-9) if w > 0 else r
        out.append(SetTarget(i + 1, new, max(lo, min(hi, p))))
    return out


def _ref(an: LiftAnalysis, load: float) -> ExpoEval | None:
    """The better (by reps) of the last two exposures at `load` that count."""
    same = [x for x in an.evals if abs(x.load - load) < 1e-6 and x.counts][-2:]
    return max(same, key=lambda x: x.total) if same else None


def _break_tier(cfg: EngineConfig, gap: int) -> BreakTier | None:
    tiers = sorted(cfg.strength.break_tiers, key=lambda t: t.min_days)
    hit = None
    for t in tiers:
        if gap >= t.min_days:
            hit = t
    return hit


def effective_top(spec: LiftSpec, cfg: EngineConfig, load: float) -> tuple[int, bool, float]:
    """(effective rep ceiling at `load`, rung infeasible?, next load) by the rung rule."""
    st = cfg.strength
    hi = spec.rep_range[1]
    nxt = _step(spec, cfg, load, +1)
    a, b = effective_load(spec, load), effective_load(spec, nxt)
    if b <= a + 1e-9 or a <= 0:
        return hi, False, nxt
    need = ceiling_needed(a, b, st.rung_min_reps)
    return max(hi, min(st.rep_ceiling_cap, need)), need > st.rep_ceiling_cap, nxt


def prescribe(
    spec: LiftSpec,
    history: Sequence[Exposure],
    cfg: EngineConfig,
    *,
    day: date | None = None,
    ctx: LiftEnv | None = None,
    analysis: LiftAnalysis | None = None,
) -> LiftPrescription:
    """Targets for the next exposure of `spec.variant` on `day`. `history` is oldest first."""
    ctx = ctx or LiftEnv()
    lo, hi = spec.rep_range
    st = cfg.strength
    hist = [e for e in history if _working(e)]
    last = hist[-1] if hist else None
    day = day or ((last.day + timedelta(days=3)) if last else date.today())
    inputs: dict[str, Any] = {
        "variant": getattr(spec.variant, "name", None),
        "rep_range": [lo, hi],
        "n_sets": spec.n_sets,
        "last": last.as_dict() if last else None,
        "exposures": len(hist),
        "recent_e1rm": [e.best_e1rm for e in hist[-(st.stall_exposures_2x + 1) :]],
    }

    def done(p: LiftPrescription) -> LiftPrescription:
        p.previous, p.inputs = last, inputs
        if p.effective_rep_range is None:
            p.effective_rep_range = (lo, hi)
        if not p.note:
            p.note = p.reason
        return p

    if spec.timed:
        p = _prescribe_timed(spec, last, cfg)
        p.state = PROGRESSING if last else GRINDING
        return done(p)
    if last is None:
        sets = [SetTarget(i + 1, None, lo) for i in range(spec.n_sets)]
        about = (lo + hi + 1) // 2
        bound = " @ " in (getattr(spec.variant, "name", None) or "")
        note = (
            f"First session at this gym: pick a weight for about {about} reps"
            if bound
            else f"New lift: pick a weight for about {about} reps"
        )
        if ctx.previous_gym and ctx.previous_weight_kg:
            reps = f" × {ctx.previous_reps}" if ctx.previous_reps else ""
            note += f" (previous gym: {ctx.previous_weight_kg:g} kg{reps}; machines differ)"
        return done(
            LiftPrescription(
                sets,
                "no_history",
                f"no history for this variant: pick a load for {lo}-{hi} reps",
                state=BASELINE,
                note=note,
                hint_weight_kg=ctx.previous_weight_kg if ctx.previous_gym else None,
            )
        )

    an = analysis or analyze(spec, hist, cfg, ctx)
    inputs.update(
        {
            "stall_counter": an.counter,
            "stall_n": an.n_required,
            "since_last_progress": an.since.isoformat() if an.since else None,
            "ladder_position": an.ladder_pos,
            "censored": an.censored(),
            "last_event": an.last.event if an.last else None,
        }
    )
    load = an.last.load if an.last else main_load(last)
    eff_hi, infeasible, nxt = effective_top(spec, cfg, load)
    base = _progress(spec, an, cfg, ctx, day, load, eff_hi, infeasible, nxt)

    # 1) a deload window covering this day: sets halved, loads kept, no +1 rep
    window = next((w for w in ctx.windows if w.covers(day)), None)
    if window is not None:
        ref = _ref(an, load) or an.last
        n = deload_set_count(spec.n_sets, cfg)
        sets = _targets(n, load, ref.reps if ref else (lo,), eff_hi, plus=0)
        why = {
            "multi_lift_stall": "several lifts stalled together",
            "fatigue_index": "fatigue index high two weeks in a row",
            "red_streak": "several red readiness days in a row",
            "legacy_reset": "several lifts stalled; replaces the planned -10% reset",
        }.get(window.reason, window.reason)
        retest = ctx.variant_id in window.retest
        note = f"deload week ({why}): {n} of {spec.n_sets} sets, same load"
        if retest:
            note += f"; retest {window.retest[ctx.variant_id].load:g} kg after the deload"
        return done(
            LiftPrescription(
                sets,
                f"deload:{window.reason}",
                f"{note} ({window.start} .. {window.until})",
                flags=["deload"] + (["retest_pending"] if retest else []),
                state=DELOAD,
                note=note,
                effective_rep_range=(lo, eff_hi),
            )
        )

    # 2) retest after a soft deload: the old load, beat the old reps
    for w in ctx.windows:
        rt = w.retest.get(ctx.variant_id) if ctx.variant_id is not None else None
        if rt is None or day <= w.until:
            continue
        if any(e.day > w.until for e in hist):
            continue  # retest done: the normal rules judge it
        top2, _inf, _n = effective_top(spec, cfg, rt.load)
        sets = _targets(spec.n_sets, rt.load, rt.reps, top2, plus=1)
        note = (
            f"Retest at old load {rt.load:g} kg: beat {_fmt_reps(rt.reps)}"
            if w.cancelled
            else f"retest {rt.load:g} kg after the deload: beat {_fmt_reps(rt.reps)}"
        )
        flags = ["retest"] + (["identical_exposures"] if _identical(hist, 3) else [])
        return done(
            LiftPrescription(
                sets,
                "retest",
                note,
                flags=flags,
                state=RETEST,
                note=note,
                effective_rep_range=(lo, top2),
                retest=True,
            )
        )
    return done(base)


def _identical(hist: Sequence[Exposure], n: int) -> bool:
    recent = list(hist)[-n:]
    return len(recent) > 1 and len({tuple((s.weight, s.reps) for s in e.sets) for e in recent}) == 1


def _progress(
    spec: LiftSpec,
    an: LiftAnalysis,
    cfg: EngineConfig,
    ctx: LiftEnv,
    day: date,
    load: float,
    eff_hi: int,
    infeasible: bool,
    nxt: float,
) -> LiftPrescription:
    st = cfg.strength
    lo, hi = spec.rep_range
    last = an.last
    assert last is not None
    hist = [x.expo for x in an.evals]
    rr = (lo, eff_hi)

    # 3) return after a break
    gap = (day - last.expo.day).days
    tier = _break_tier(cfg, gap)
    if tier is not None:
        new = reduce_load(spec, cfg, load, tier.load_pct) if tier.load_pct > 0 else load
        n = spec.n_sets - tier.sets_minus
        if tier.sets_factor < 1:
            n = math.ceil(spec.n_sets * tier.sets_factor - 1e-9)
        n = max(1, n)
        if new != load:
            sets = _predicted_targets(spec, n, load, new, last.reps, lo, hi)
        else:
            sets = _targets(n, load, last.reps, eff_hi, plus=0)
        cut = f"-{tier.load_pct:g}% to {new:g} kg" if new != load else "same load"
        note = f"back after {gap} days: {cut}, {n} of {spec.n_sets} sets; misses don't count yet"
        return LiftPrescription(
            sets,
            "return_after_break",
            note,
            flags=["break"],
            state=REBUILD,
            note=note,
            effective_rep_range=rr,
        )
    # the first week back from a long break keeps the set cut
    for i in range(len(hist) - 1, max(0, len(hist) - 3), -1):
        g = (hist[i].day - hist[i - 1].day).days
        t = _break_tier(cfg, g)
        if t is not None and t.days > 1 and (day - hist[i].day).days < t.days:
            n = spec.n_sets - t.sets_minus
            if t.sets_factor < 1:
                n = math.ceil(spec.n_sets * t.sets_factor - 1e-9)
            n = max(1, n)
            sets = _targets(n, load, (_ref(an, load) or last).reps, eff_hi, plus=1)
            note = f"first week back after {g} days: {n} of {spec.n_sets} sets"
            return LiftPrescription(
                sets,
                "return_after_break",
                note,
                flags=["break"],
                state=REBUILD,
                note=note,
                effective_rep_range=rr,
            )

    # 4) a ladder step decided earlier and not trained yet: the same prescription again
    if ctx.ladder:
        ev = max(ctx.ladder, key=lambda e: (e.day, e.step))
        if not any(x.day >= ev.day for x in hist):
            return _ladder_step(spec, an, cfg, day, load, eff_hi, hist, pending=ev)

    # 5) stall -> ladder
    if an.stalled and ctx.allow_ladder:
        return _ladder_step(spec, an, cfg, day, load, eff_hi, hist)

    # 6) first exposure(s) at a new load far below the floor
    first_set = last.reps[0] if last.reps else 0
    at_load = [x for x in an.evals if abs(x.load - load) < 1e-6]
    run = 0  # consecutive latest exposures at this load
    for x in reversed(an.evals):
        if abs(x.load - load) > 1e-6:
            break
        run += 1
    before = an.evals[-run - 1] if len(an.evals) > run else None
    new_load = run <= 2 and (before is None or _harder(spec, load, before.load))
    judged = last.censored is None or last.censored.startswith("first sessions")
    if (
        st.decrease_load_if_first_set_below_range
        and first_set < lo
        and load > 0
        and new_load
        and judged
    ):
        if run >= 2 or first_set <= st.jump_abort_reps:
            down = _step(spec, cfg, load, -1)
            sets = _predicted_targets(spec, spec.n_sets, load, down, last.reps, lo, hi)
            note = (
                f"first set only {first_set} reps at the new load {load:g} kg -> back to "
                f"{down:g} kg"
            )
            return LiftPrescription(
                sets,
                "double_progression:decrease_load",
                note,
                flags=["load_down"],
                state=REBUILD,
                note=note,
            )
        sets = _targets(spec.n_sets, load, last.reps, eff_hi, plus=1)
        note = f"new load {load:g} kg: first set {first_set} (< {lo}), one more try"
        return LiftPrescription(
            sets,
            "double_progression:hold_new_load",
            note,
            state=GRINDING,
            note=note,
            effective_rep_range=rr,
        )

    # 7) real drop vs noise (only clean exposures count)
    if last.censored is None and last.miss:
        prev_clean = [x for x in an.evals[:-1] if abs(x.load - load) < 1e-6 and x.censored is None]
        two = bool(prev_clean) and prev_clean[-1].miss and prev_clean[-1].drop > 0
        if last.drop >= st.big_miss_reps or two:
            down = _step(spec, cfg, load, -1)
            sets = _predicted_targets(spec, spec.n_sets, load, down, last.reps, lo, hi)
            why = (
                f"{last.drop} reps fewer than last time"
                if last.drop >= st.big_miss_reps
                else "two clean drops in a row"
            )
            note = f"{why} at {load:g} kg -> {down:g} kg and rebuild"
            return LiftPrescription(
                sets,
                "double_progression:decrease_load",
                note,
                flags=["load_down"],
                state=REBUILD,
                note=note,
            )
        ref = _ref(an, load) or last
        sets = _targets(spec.n_sets, load, ref.reps, eff_hi, plus=1)
        plural = "s" if last.drop != 1 else ""
        note = f"{last.drop} rep{plural} fewer than last time: noise, same target"
        return LiftPrescription(
            sets,
            "double_progression:hold_noise",
            note,
            state=GRINDING,
            note=note,
            effective_rep_range=rr,
        )

    # 8) gate: all sets at the top once, or every set >= top-1 on two consecutive clean exposures
    counting = [x for x in at_load if x.counts]
    all_top = bool(last.reps) and all(r >= eff_hi for r in last.reps) and last.counts
    near = [x for x in counting[-2:] if x.reps and all(r >= eff_hi - 1 for r in x.reps)]
    two_strike = len(counting) >= 2 and len(near) == 2
    raised = eff_hi > hi
    if all_top or two_strike:
        if infeasible and eff_hi >= st.rep_ceiling_cap:
            sets = _targets(spec.n_sets, load, last.reps, eff_hi, plus=1)
            note = (
                f"the next rung {nxt:g} kg is too big a jump even from {eff_hi} reps: "
                "needs a smaller increment or a variation (coach)"
            )
            return LiftPrescription(
                sets,
                "rung_infeasible",
                note,
                flags=["rung_infeasible"],
                state=STALLED,
                note=note,
                effective_rep_range=rr,
            )
        src = last.reps if all_top else max(near, key=lambda x: x.total).reps
        sets = _predicted_targets(spec, spec.n_sets, load, nxt, src, lo, hi)
        pred = sets[0].reps
        how = "every set at the top" if all_top else f"every set >= {eff_hi - 1} twice"
        note = f"{how} at {load:g} kg -> {nxt:g} kg (Epley predicts ~{pred} reps)"
        return LiftPrescription(
            sets,
            "double_progression:increase_load",
            note,
            state=PROGRESSING,
            note=note,
        )

    # 9) +1 rep per set on the better of the last two
    ref = _ref(an, load) or last
    sets = _targets(spec.n_sets, load, ref.reps, eff_hi, plus=1)
    state = PROGRESSING if last.event else GRINDING
    note = f"+1 rep per set: beat {_fmt_reps(ref.reps)} @ {load:g} kg"
    flags: list[str] = []
    if raised:
        note += (
            f"; ceiling {eff_hi} for now: {nxt:g} kg from {hi} reps would predict fewer than "
            f"{st.rung_min_reps}"
        )
        flags.append("ceiling_raised")
    if infeasible:
        flags.append("rung_infeasible")
    return LiftPrescription(
        sets,
        "double_progression:add_reps",
        f"same load, each set +1 rep (cap {eff_hi}): last {_fmt_reps(last.reps)} @ {load:g} kg",
        flags=flags,
        state=state,
        note=note,
        effective_rep_range=rr,
    )


def _ladder_step(
    spec: LiftSpec,
    an: LiftAnalysis,
    cfg: EngineConfig,
    day: date,
    load: float,
    eff_hi: int,
    hist: list[Exposure],
    pending: LadderEvent | None = None,
) -> LiftPrescription:
    """The next stall-ladder step (or the `pending` one again, worded identically, so a re-run
    of the engine before the step was trained leaves the planned session unchanged)."""
    st = cfg.strength
    lo, hi = spec.rep_range
    last = an.last
    assert last is not None
    step = pending.step if pending is not None else an.ladder_pos + 1
    peak = (
        pending.peak_e1rm
        if pending is not None
        else max((x.e1rm for x in an.evals if x.e1rm is not None), default=None)
    )
    flags = ["stall"] + (["identical_exposures"] if _identical(hist, an.n_required) else [])
    why = (
        f"no progress (load, reps or e1RM +{st.e1rm_progress_pct:g}%) in {an.n_required} or more "
        f"clean sessions"
    )
    if step in (LADDER_RESET_SMALL, LADDER_RESET_LARGE):
        pct = st.stall_reset_small_pct if step == LADDER_RESET_SMALL else st.stall_reset_pct
        new = pending.load if pending is not None and pending.load is not None else None
        if new is None:
            new = reduce_load(spec, cfg, load, pct)
        sets = _predicted_targets(spec, spec.n_sets, load, new, last.reps, lo, hi)
        tail = "" if new != load else " (already the lowest available load)"
        note = f"stall ladder step {step}: -{pct:g}% to {new:g} kg{tail}, rebuild"
        return LiftPrescription(
            sets,
            "stall:reset_small" if step == LADDER_RESET_SMALL else "stall:reset_large",
            f"{why}; {note}",
            flags=flags,
            stall=True,
            state=REBUILD,
            note=note,
            ladder=pending or LadderEvent(day, step, new, peak, load),
        )
    ref = _ref(an, load) or last
    sets = _targets(spec.n_sets, load, ref.reps, eff_hi, plus=1)
    exhausted = step > LADDER_RESET_LARGE
    note = (
        f"stalled again after a -{st.stall_reset_small_pct:g}% reset: the coach proposes a rep "
        "range change or a variation; same load until then"
        if not exhausted
        else "stall ladder exhausted: the coach decides (variation, rep range, sets)"
    )
    return LiftPrescription(
        sets,
        "stall:advisor",
        f"{why}; {note}",
        flags=flags + (["ladder_exhausted"] if exhausted else []),
        stall=True,
        state=STALLED,
        note=note,
        effective_rep_range=(lo, eff_hi),
        ladder=pending or LadderEvent(day, step, load, peak, load),
    )


def _prescribe_timed(spec: LiftSpec, last: Exposure | None, cfg: EngineConfig) -> LiftPrescription:
    step = cfg.strength.timed_step_s
    if last is None:
        sets = [SetTarget(i + 1, None, None, duration_s=None) for i in range(spec.n_sets)]
        return LiftPrescription(sets, "no_history", "no history: hold as long as you can")
    best = max((s.duration_s or 0.0) for s in _working(last))
    sets = [SetTarget(i + 1, None, None, duration_s=best + step) for i in range(spec.n_sets)]
    return LiftPrescription(
        sets,
        "timed_progression",
        f"best hold last time {best:g}s -> target {best + step:g}s per set",
    )
