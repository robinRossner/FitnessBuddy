"""Advisor triggers from running metrics: stagnation and a possible 4th run."""

from __future__ import annotations

from datetime import timedelta

from apps.fitness.engine.core import running as R
from apps.fitness.engine.core.planner.models import (
    DecisionRec,
    TriggerRec,
)
from apps.fitness.engine.core.planner.state import PlannerState


class TriggersMixin(PlannerState):
    """Stagnation and 4th-run advisor triggers."""

    # ------------------------------------------------------------------ stagnation / 4th run
    def _stagnation_triggers(self) -> None:
        rc = self.rc
        series = []
        for r in self.inp.runs:
            if r.km >= 3 and R.split_intervals(r.laps) is None:
                ef = R.efficiency(r.distance_m, r.duration_s, r.avg_hr)
                if ef:
                    series.append((r.day, ef))
        s = R.stagnation(series, self.today, rc.stagnation_weeks, rc.stagnation_min_improvement_pct)
        if s:
            self.triggers.append(
                TriggerRec(
                    "running_stagnation",
                    f"running_stagnation:ef:{self.inp.week_start}",
                    {"metric": "aerobic_efficiency", **s},
                    "week",
                    self.inp.week_start.isoformat(),
                    self.today,
                )
            )
            self.decisions.append(
                DecisionRec(
                    "running",
                    "running:stagnation",
                    f"aerobic efficiency {s['change_pct']:+.1f}% vs the previous {s['weeks']} weeks",  # noqa: E501
                    {"series_points": len(series)},
                    s,
                    "week",
                    self.inp.week_start.isoformat(),
                )
            )

    def _fourth_run_trigger(self) -> None:
        fr = self.rc.fourth_run
        ws = self.inp.week_start
        vh = R.volume_history(self.inp.runs, self.today, ws, self.inp.open_run_km)
        weeks = []  # the last complete weeks: an unfinished week has no final run count
        w = ws - timedelta(days=7)
        while len(weeks) < fr.consistent_weeks:
            if w not in vh.unfinished:
                weeks.append(w)
            w -= timedelta(days=7)
        counts = {w: 0 for w in weeks}
        for r in self.inp.runs:
            w = R.iso_week_start(r.day)
            if w in counts:
                counts[w] += 1
        consistent = all(c >= fr.min_runs_per_week for c in counts.values())
        mean4 = R.mean_km(vh.weekly, ws, 4, vh.skip)
        longest = max(
            (r.km for r in self.inp.runs if r.day >= ws - timedelta(days=28)), default=0.0
        )
        if consistent and mean4 >= fr.min_weekly_km and longest >= fr.min_long_run_km:
            self.triggers.append(
                TriggerRec(
                    "fourth_run",
                    f"fourth_run:{ws.year}-{ws.month:02d}",
                    {
                        "weeks_consistent": fr.consistent_weeks,
                        "mean_km_4w": mean4,
                        "longest_km_4w": round(longest, 1),
                        "suggestion": "an easy 30-40 min run on Thursday after upper or on "
                        "Monday, never Tuesday; lifting stays at 3 per week",
                    },
                    "week",
                    ws.isoformat(),
                    ws,
                )
            )
            self.decisions.append(
                DecisionRec(
                    "running",
                    "running:fourth_run_ready",
                    f"{fr.consistent_weeks} consistent weeks, {mean4:g} km/week, longest "
                    f"{longest:.1f} km: a 4th run is suggested to the coach (not added)",
                    {"runs_per_week": {w.isoformat(): c for w, c in sorted(counts.items())}},
                    {"suggested": True},
                    "week",
                    ws.isoformat(),
                )
            )
