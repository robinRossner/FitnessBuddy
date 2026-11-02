"""The shared state of one planning run, declared for the type checker.

`_Planner.__init__` (`week.py`) and the mixins' preparation steps set these attributes; every
mixin subclasses `PlannerState`, so the attributes and the methods one mixin calls on another are
known statically. Annotations and `TYPE_CHECKING` stubs only: nothing here exists at runtime.
"""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from apps.fitness.engine.core import quality as Q
    from apps.fitness.engine.core import running as R
    from apps.fitness.engine.core import vdot as V
    from apps.fitness.engine.core.config import (
        EngineConfig,
        ReadinessConfig,
        RecoveryConfig,
        RunningConfig,
        StrengthConfig,
    )
    from apps.fitness.engine.core.hr import HrZones, ZoneBook
    from apps.fitness.engine.core.pace import PaceModel
    from apps.fitness.engine.core.planner.models import DecisionRec, PlanInputs, TriggerRec
    from apps.fitness.engine.core.readiness import Readiness


class PlannerState:
    # set in `_Planner.__init__`
    inp: PlanInputs
    cfg: EngineConfig
    rc: RunningConfig
    rd: ReadinessConfig
    rec: RecoveryConfig
    st: StrengthConfig
    zone_book: ZoneBook
    zones: HrZones
    model: PaceModel
    decisions: list[DecisionRec]
    triggers: list[TriggerRec]
    _increases: dict[str, list[str]]
    today: date
    readiness_by_day: dict[date, str]
    today_readiness: Readiness
    colors: dict[date, str]
    hard_run_days: set[date]
    # set by `PaceMixin._prepare_running`
    phase: Q.PhaseState
    vdot_update: V.VdotUpdate
    quality_plan: Q.QualityPlan
    quality_last: tuple[Q.QualityRecord | None, Any]
    quality_off: bool
    break_days: int | None
    # set by `RunVolumeMixin`
    _long: R.LongRunPlan
    _long_eval: R.EasyEval | None
    _wu: float
    _cd: float
    _rec_m: float

    if TYPE_CHECKING:
        # implemented in one mixin, called from another

        def _easy_paces(self) -> dict[str, Any]: ...

        def _easy_prescription(
            self, km: float, reason: str, *, strides: bool = True
        ) -> dict[str, Any]: ...

        def _quality_km(self) -> float: ...
