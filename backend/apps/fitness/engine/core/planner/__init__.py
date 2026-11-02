"""The weekly planner: plain data in, a `WeekPlan` (sessions + decisions + advisor triggers) out.

No database access here (see `engine/store/data.py` for loading and `persist.py` for writing), and
everything tunable comes from the config object. Steps for each (not yet frozen) day from today:

1. template week (config `weekly_template`) -> recovery rules (no intervals right after heavy
   legs, swap algorithm in `scheduling.py`);
2. readiness for today (deterministic, `readiness.py`), red-streak -> deload window;
3. lifting (`lifting.py`): every lift is analysed first (stall, censored misses, ladder); three
   or more stalls within 14 days become ONE soft deload week + a retest instead of per-lift
   resets, a high fatigue index becomes a reactive deload; then the per-slot prescriptions;
4. running: VDOT state (`vdot.py`), the Friday quality cycle (`quality.py`), the long run
   (step-back week, drift gate), the easy run with strides, all fitted under the weekly cap
   (4-week actual mean, `running.py`) and the single-run cap;
5. today's readiness adjustments are written INTO the planned session (yellow: -1 set / easy
   run, red: rest or short easy run) together with the reason;
6. every rule that fired is returned as a decision record; stalls, deloads, rule conflicts,
   HRV anomalies, stagnating running metrics and a possible 4th run also as advisor triggers.

Specs: docs/research/lifting-progression.md, docs/research/running-progression.md and the
decisions in docs/spec/decisions.md ("Progression engine v2").


Package layout (one module per seam of the former planner.py): `models` (inputs/outputs),
`helpers`, `week` (`_Planner`: state, step order, one day), `paces`, `readiness_rules`,
`lifting_week`, `run_volume`, `run_days`, `triggers`.
"""

from apps.fitness.engine.core.planner.helpers import (
    _TITLES,
    _hr,
    _ladder_dict,
    _paces,
    _round1,
    work_km,
)
from apps.fitness.engine.core.planner.models import (
    DayPlan,
    DecisionRec,
    LiftContext,
    PlanInputs,
    TriggerRec,
    VariantInfo,
    WeekPlan,
)
from apps.fitness.engine.core.planner.week import _Planner, generate_week
from apps.fitness.engine.core.windows import window_dict as _window_dict

__all__ = [
    "_TITLES",
    "DayPlan",
    "DecisionRec",
    "LiftContext",
    "PlanInputs",
    "TriggerRec",
    "VariantInfo",
    "WeekPlan",
    "_Planner",
    "_hr",
    "_ladder_dict",
    "_paces",
    "_round1",
    "_window_dict",
    "generate_week",
    "work_km",
]
