# Stats API (step 5 contract)

Pre-aggregated, read-only numbers for the iOS Progress and Health dashboards. Everything is
computed on the server from the same tables and the same engine code the planner uses, so the app
never recomputes e1RM, readiness, HR zones or interval verdicts itself.

- Base URL `https://<host>/api/v1`, `Authorization: Bearer <API_TOKEN>` on every endpoint (401
  otherwise). All endpoints are `GET`.
- Conventions as in [sync.md](sync.md): snake_case, `date` = `YYYY-MM-DD` (user's local day),
  timestamps ISO-8601 UTC with milliseconds and `Z`. Units: weights **kg**, distances **metres**,
  durations **seconds**, paces **seconds per km**. Unknown values are `null`, never `NaN`.
- Every response has `"generated_at"` (when the numbers were computed; cached responses keep
  their original value).
- Real example payloads (a copy of the real database, "today" pinned to 2026-10-25) are in
  [`examples/stats_*.json`](examples/); arrays there may be trimmed, see their `_note`.
- Speed: every endpoint answers in well under 300 ms on the real database (cold, worst case about
  60 ms for `/stats/lifts`, the rest below 25 ms). Responses are cached in-process and recomputed
  automatically after any new data (Garmin sync, sync push, stats rebuild, config change) or when
  the day changes.

| Path | Query | Example file |
|---|---|---|
| `/stats/lifts` | | `stats_lifts.json` |
| `/stats/lifts/{variant_id}` | `from`, `to` | `stats_lift_detail.json` |
| `/stats/volume/weekly` | `weeks=26` | `stats_volume_weekly.json` |
| `/stats/running` | `weeks=26` | `stats_running.json` |
| `/runs/{run_id}/analysis` | | `stats_run_analysis_intervals.json`, `stats_run_analysis_easy.json` |
| `/stats/health` | `days=90` | `stats_health.json` |
| `/stats/correlations` | `days=120` | `stats_correlations.json` |

`weeks` is 1..520, `days` 1..3650 (422 otherwise). Unknown `variant_id` (or one without any
session) and unknown `run_id` give 404.

## GET /stats/lifts

One summary per exercise variant that has at least one session, **sorted by `last_date`
descending** (ties: name). `GET /stats/lifts/{id}` returns the same object as `lift`.

```json
{
  "generated_at": "2026-10-25T11:24:35.995Z",
  "lifts": [
    {
      "variant_id": 127,
      "name": "Triceps Extension (Cable)",
      "exercise_id": 9,
      "muscle_group": "triceps",
      "body_region": "Arms",
      "gym_id": 4,
      "gym_name": "Gym 3",
      "active": true,
      "in_program": true,
      "sessions": 9,
      "first_date": "2026-08-17",
      "last_date": "2026-10-23",
      "best_e1rm": 91.0,
      "latest_e1rm": 91.0,
      "trend_4w_pct": 0.0,
      "last_pr_date": null,
      "stalled": true
    }
  ]
}
```

- `name`: the variant name without the gym suffix (the gym is in `gym_name`; `null` = generic
  variant that is the same in every gym). `muscle_group` is the exercise's primary muscle.
- `body_region`: coarse region for dashboards, one of `Chest` | `Back` | `Shoulders` | `Arms` |
  `Legs` | `Core` | `Other`. It is stored per exercise in `apps/fitness/data/catalog/exercises.yaml`
  (`body_region`); rear delts count as Shoulders, lats and traps as Back, biceps / triceps /
  forearms as Arms, glutes / hamstrings / quads / calves / adductors as Legs, abs as Core,
  anything else (e.g. cardio) as Other. Exercises that are not in the catalog derive it from
  `muscle_group` by the same table.
- `in_program`: `true` when the variant is one the active exercises of `apps/fitness/data/program/program.yaml`
  resolve to at the current gym (the generic variant or the one bound to that gym, exactly as the
  planner picks it). `false` for variants of other gyms, exercises not in the program and when
  there is no program file.
- `active`: `false` = the user no longer does it (history is kept).
- `best_e1rm` / `latest_e1rm`: best set e1RM ever / in the newest session that has one. e1RM is
  the engine's bodyweight-aware value (Epley; bodyweight + added weight for pull-ups and dips,
  bodyweight - assistance for assisted machines; per-dumbbell variants stay per dumbbell). `null`
  for lifts without a meaningful e1RM (timed holds, band pull-ups).
- `trend_4w_pct`: best e1RM of the last 28 days vs the best of the 28 days before, in %. `null`
  when either window has no session (e.g. not trained recently).
- `last_pr_date`: newest session with an e1RM PR (the first session of a lift is a baseline, not
  a PR).
- `stalled`: only for `active` variants: the engine's stall rule (`detect_stall`, no best-e1RM
  improvement in the last `strength.stall_exposures` exposures, counted since the last stall reset
  the engine decided) **or** an open `lift_stall` advisor trigger for the variant.

## GET /stats/lifts/{variant_id}?from=&to=

`from` / `to` (inclusive, `YYYY-MM-DD`) restrict `points` and `prs`; default is all history. The
`lift` summary is always all-history. Both arrays are **oldest first**.

```json
{
  "generated_at": "2026-10-25T11:24:36.044Z",
  "lift": {
    "variant_id": 106,
    "name": "Lateral Raise (Machine)",
    "exercise_id": 7,
    "muscle_group": "shoulders",
    "gym_id": 3,
    "gym_name": "Gym 2",
    "active": true,
    "sessions": 97,
    "first_date": "2025-02-03",
    "last_date": "2026-08-19",
    "best_e1rm": 66.67,
    "latest_e1rm": 66.67,
    "trend_4w_pct": null,
    "last_pr_date": "2025-11-22",
    "stalled": true
  },
  "points": [
    {
      "date": "2026-08-12",
      "session_id": "9e770d8b-4755-5b97-9784-985745d7b399",
      "best_e1rm": 66.67,
      "top_set": {
        "weight_kg": 50.0,
        "reps": 10
      },
      "volume_kg": 1350.0,
      "sets": 3
    },
    {
      "date": "2026-08-19",
      "session_id": "500309af-9aa4-5a97-bb51-2883f0a1e2c9",
      "best_e1rm": 66.67,
      "top_set": {
        "weight_kg": 50.0,
        "reps": 10
      },
      "volume_kg": 1350.0,
      "sets": 3
    }
  ],
  "prs": [
    {
      "date": "2025-10-18",
      "type": "reps",
      "value": 9,
      "weight_kg": 50.0,
      "reps": 9
    },
    {
      "date": "2025-11-22",
      "type": "e1rm",
      "value": 66.67,
      "weight_kg": 50.0,
      "reps": 10
    },
    {
      "date": "2025-11-22",
      "type": "reps",
      "value": 10,
      "weight_kg": 50.0,
      "reps": 10
    }
  ]
}
```

(`points` and `prs` shortened here.)

- `points`: one per session: `best_e1rm` (may be `null`), `top_set` (heaviest working set, ties by
  reps; `null` if no weighted set), `volume_kg` (sum of weight x reps over working / failure /
  drop sets, in the variant's logged convention), `sets` (non-warm-up sets).
- `prs`: `type` is `e1rm` (`value` = the new best e1RM, `weight_kg` x `reps` = the set that
  produced it), `weight` (heaviest weight so far, `value` = kg) or `reps` (more reps than ever
  at a weight already used, `value` = reps). One session can have several entries.

## GET /stats/volume/weekly?weeks=26

`weeks` weeks ending with the current one, **oldest first**, every week present even when empty.
`week_start` is the Monday.

```json
{
  "generated_at": "2026-10-25T11:24:36.050Z",
  "weeks": [
    {
      "week_start": "2026-10-19",
      "lifting_volume_kg": 19893.0,
      "lifting_sets": 39,
      "lifting_sessions": 2,
      "planned_lifting_sessions": 3,
      "prs": 0,
      "pr_lifts": 0,
      "sets_by_muscle_group": {
        "abs": 4,
        "adductors": 2,
        "back": 5,
        "biceps": 3,
        "calves": 3,
        "chest": 6,
        "hamstrings": 3,
        "lats": 2,
        "quads": 5,
        "shoulders": 3,
        "triceps": 3
      },
      "running_km": 8.13,
      "running_time_s": 2600,
      "runs": 1
    }
  ]
}
```

- `lifting_volume_kg`: sum of the lift `volume_kg` over all variants (per-dumbbell lifts count
  one dumbbell). `lifting_sets`: non-warm-up sets = the sum of `sets_by_muscle_group`.
- `sets_by_muscle_group`: working sets by the exercise's primary muscle; only groups trained.
- `prs`: number of **sessions** that week with at least one e1RM or weight PR on an `active`
  lift (PRs on retired lifts are ignored). `pr_lifts`: number of **distinct lifts** (variants)
  with such a PR that week. Rep PRs do not count.
- `planned_lifting_sessions`: upper / lower sessions in `planned_sessions` for that week
  (soft-deleted ones ignored) when the week is fully planned (a row for every day of the
  engine's weekly template, rest days included, which is how the planner stores it). Otherwise,
  including a week with only a carried-over Sunday plan, the template count, 3 (`upper`,
  `lower`, `upper`).
- `running_*`: running activities only (hiking etc. excluded), by local start day.

## GET /stats/running?weeks=26

```json
{
  "generated_at": "2026-10-25T11:24:36.071Z",
  "kpis": {
    "vdot_current": 44.51,
    "vdot_4w_ago": 43.44,
    "vdot_change": 3.0,
    "vdot_source": "interval_reps",
    "vdot_fit_current": 41.0,
    "efficiency_current": 1.2656,
    "efficiency_4w_ago": 1.2982,
    "efficiency_change_pct": -2.51,
    "easy_hr_drift_flags_4w": 0,
    "runs_this_week": 1,
    "runs_planned_this_week": 3,
    "km_this_week": 8.13
  },
  "weekly": [
    {
      "week_start": "2026-10-19",
      "km": 8.13,
      "time_s": 2600,
      "runs": 1,
      "time_in_zones_s": {
        "z1": 1639,
        "z2": 730,
        "z3": 108,
        "z4": 96,
        "z5": 24
      },
      "runs_detail": [
        {"run_id": "0e7a552a-043e-5034-b515-f4b63639274b", "date": "2026-10-22", "type": "long", "km": 8.13}
      ],
      "runs_by_type": {"easy": 0, "intervals": 0, "long": 1, "other": 0}
    }
  ],
  "efficiency": [
    {
      "date": "2026-10-22",
      "run_id": "0e7a552a-043e-5034-b515-f4b63639274b",
      "type": "long",
      "distance_m": 8126.0,
      "avg_hr": 151.0,
      "avg_pace_s_per_km": 319.9,
      "efficiency": 1.2419,
      "decoupling_pct": -1.1
    }
  ],
  "vdot": [
    {
      "date": "2026-09-14",
      "value": 43.44,
      "source": "initial",
      "delta": 0.0,
      "reason": "initial VDOT 41.0 from the pace model's fit"
    },
    {
      "date": "2026-10-19",
      "value": 44.51,
      "source": "interval_reps",
      "delta": 3.0,
      "reason": "VDOT 42.4 -> 45.4 measured from interval reps: 6 x 800 m on 2026-08-26 ... (capped at +3 per update)"
    }
  ],
  "vdot_fit": [
    {
      "date": "2026-10-15",
      "value": 41.0
    },
    {
      "date": "2026-10-22",
      "value": 41.0
    }
  ],
  "intervals": [
    {
      "date": "2026-09-16",
      "run_id": "3bffec89-6500-561b-be66-a9a6a35236aa",
      "reps": 5,
      "target_pace_s_per_km": 269.1,
      "avg_work_pace_s_per_km": 249.7,
      "avg_work_hr": 165.8,
      "target_hr_low": 166,
      "target_hr_high": 179,
      "hr_in_range": false,
      "recovery_ok": true,
      "passed": true
    }
  ]
}
```

(each array shortened here; `weekly` has an entry for every week, oldest first, zeros when idle.)

- `kpis` (top level): headline numbers for the running dashboard, always relative to today and
  independent of `weeks` (they look back 4 weeks plus the VDOT lookback even for `weeks=1`).
  `null` means there is not enough data:
  - `vdot_current`: the engine's quality VDOT, the number the plan's paces (threshold, interval,
    easy range) come from: the last `running:vdot` record of the decision log, identical to the
    planner's VDOT state (2 decimals, e.g. 44.51). `vdot_4w_ago`: the record in force 28 days
    ago, `vdot_change` (difference); `null` when there was no record yet then. All three are
    `null` while the engine has not logged a VDOT (before its first run). `vdot_source`: the
    `source` of the current record (see `vdot` below). `vdot_fit_current`: the secondary
    race-fit estimate of today (see `vdot_fit`), `null` while it is only the seed race.
  - `efficiency_current`: mean `efficiency` of the last 3 easy or long runs. `efficiency_4w_ago`:
    mean of the 3 such runs closest to 28 days before today, other than the current 3 and
    within 14 days of that date. `efficiency_change_pct`: (current - 4w ago) / 4w ago in %.
    Each is `null` with fewer than 3 runs.
  - `easy_hr_drift_flags_4w`: easy runs in the last 28 days whose `decoupling_pct` is above the
    engine's flag (`running.easy.decoupling_flag_pct`, 5 %). Always a number (0 when none).
  - `runs_this_week`, `km_this_week`: runs and kilometres since this week's Monday (0 when none).
    `runs_planned_this_week`: easy / long / interval runs planned this week, with the same
    full-week-or-template rule as `planned_lifting_sessions` (template: 3 runs).
- `weekly.runs_detail`: the week's runs, oldest first: `run_id` (use with `/runs/{id}/analysis`),
  `date`, `km`, and `type` = `easy` | `intervals` | `long` | `other`, the same classification as
  `efficiency[].type` below. `weekly.runs_by_type`: count of runs per type (sums to `runs`).
  `other` is reserved and is 0 for now: every run is classified into one of the three.
- `weekly.time_in_zones_s`: seconds in HR zones **z1..z5**, using the engine's *current* zones
  (never stored). With `running.heart_rate.source: garmin` and valid Garmin floors these are Garmin's
  five zones from the latest floors; a run with the same floors shows Garmin's seconds as they are,
  older runs are re-binned so all bars use today's zones. Without usable floors they come from LTHR:
  Friel's 7 running zones (`z1, z2, z3, z4, z5a, z5b, z5c`) collapsed into five:

  | API zone | Friel zones | at LTHR 171 |
  |---|---|---|
  | `z1` | z1 | up to 145 bpm |
  | `z2` | z2 | 146-152 (top = easy-run ceiling) |
  | `z3` | z3 | 153-161 |
  | `z4` | z4 | 162-169 |
  | `z5` | z5a + z5b + z5c | 170 and up |

  Re-binning assumes the time inside one Garmin zone is evenly spread over its bpm range.
  Runs without Garmin zone data use their laps: each lap's duration goes to the zone of its
  average HR. Runs with neither count as 0.
- `efficiency` (easy and long runs, oldest first): `efficiency` = **metres per minute per bpm**
  = (distance_m / (duration_s / 60)) / avg_hr; higher = fitter. `decoupling_pct` = pace:HR drift
  between the first and second half (positive = HR drifted up; `null` under 4 laps with HR).
  `type`: the linked plan's kind, else `intervals` if the title / laps show an interval session,
  else `long` from `running.long_run.min_km` (8 km), else `easy`.
- `vdot`: the engine's quality VDOT as a step series, oldest first: one point per change in the
  decision log (`running:vdot`, see engine/vdot.py); the value holds until the next point (a chart
  draws a step and extends the last value to today). The whole history is returned, independent
  of `weeks`; it is empty before the engine's first run. Fields: `date` (the day of the
  update), `value` (VDOT, 2 decimals), `source` (`initial` the pace model's fit anchors the first
  record, `effort` a race / time trial / hard effort, `interval_reps` measured from interval
  reps, `green_sessions` quality sessions passed easily, `interval_pace` the pace lever,
  `layoff` after a break, `fails` after repeated missed quality paces), `delta` (change vs the
  previous value, 0 for `initial`) and `reason` (the decision's rationale, a sentence, may be
  `null`). Older builds read only `date` and `value`, so they now chart this series too.
- `vdot_fit`: secondary, one point per run day in the window (`date`, `value`): the pace
  model's race-fit estimate on that day (seed from the config, raised by the best hard effort of
  the last `vdot.lookback_weeks`). It is NOT what the plan uses; it averages reps and jogs and
  can sit well below the quality VDOT.
- `intervals`: interval sessions (engine `split_intervals` / `evaluate_intervals`).
  `target_pace_s_per_km`: from the linked planned session, else the pace model's interval pace on
  that day. `target_hr_low/high`: the interval HR zone in force for that run (z4 to z5a from LTHR, z5 from
  Garmin's floors). `hr_in_range`, `recovery_ok`
  (HR drop in the recovery laps vs `intervals.hr_recovery_min_bpm`) may be `null`; both are
  context only. `passed` is the engine's verdict, judged by PACE since the progression engine v2
  (>= 80 % of the reps within the tolerance, last rep <= 3 % slower than the first, all reps done).

## GET /runs/{run_id}/analysis

Works for any run id of `/api/v1/runs`. Examples: `stats_run_analysis_intervals.json`,
`stats_run_analysis_easy.json`.

```json
{
  "generated_at": "2026-10-25T11:24:36.087Z",
  "run": {
    "id": "3bffec89-6500-561b-be66-a9a6a35236aa",
    "date": "2026-09-16",
    "type": "intervals",
    "name": "Feldkirchen - 5x1000/400 Intervals",
    "distance_m": 10023.1,
    "duration_s": 2902.6,
    "avg_hr": 156.0,
    "max_hr": 179.0,
    "avg_pace_s_per_km": 289.6,
    "training_effect_aerobic": 3.8,
    "training_effect_anaerobic": 0.6
  },
  "planned": null,
  "laps": [
    {
      "index": 1,
      "kind": "warmup",
      "distance_m": 1000.0,
      "duration_s": 311.9,
      "avg_pace_s_per_km": 311.9,
      "avg_hr": 142.0,
      "max_hr": 159.0,
      "target_pace_s_per_km": null,
      "target_hr_low": null,
      "target_hr_high": null,
      "pace_ok": null,
      "hr_ok": null
    },
    {
      "index": 3,
      "kind": "work",
      "distance_m": 1000.0,
      "duration_s": 253.4,
      "avg_pace_s_per_km": 253.4,
      "avg_hr": 166.0,
      "max_hr": 171.0,
      "target_pace_s_per_km": 269.1,
      "target_hr_low": 179,
      "target_hr_high": 189,
      "pace_ok": true,
      "hr_ok": true
    },
    {
      "index": 4,
      "kind": "recovery",
      "distance_m": 400.0,
      "duration_s": 141.5,
      "avg_pace_s_per_km": 353.7,
      "avg_hr": 152.0,
      "max_hr": 172.0,
      "target_pace_s_per_km": null,
      "target_hr_low": null,
      "target_hr_high": null,
      "pace_ok": null,
      "hr_ok": null
    }
  ],
  "verdict": {
    "passed": true,
    "summary": "5 reps at 4:10/km, work HR 166: passed easily.",
    "reasons": [
      "5 of 5 reps on pace",
      "last rep -1.7% vs the first",
      "last rep peaked at 173 bpm: passed easily",
      "Reps ran ~7% faster than target 4:29/km; the target will catch up via VDOT"
    ]
  }
}
```

(`laps` shortened here.)

- `run.type`: `easy` | `long` | `intervals`, or `other` for non-running activities.
- `planned`: the linked planned session in the same shape as `/plan/week` items (`id`, `kind`,
  `title`, `session`, ...), else `null`.
- `laps[].kind`: `warmup` | `work` | `recovery` | `cooldown` | `other`. From the FIT lap
  intensity; if that does not separate work from recovery the engine's pace heuristic is used and
  laps before / after the work laps are warm-up / cool-down. Easy and long runs have `other`
  laps (unless the watch marked warm-up / cool-down).
- Targets: `work` laps of an interval run get the target pace (`pace_ok` = true unless the
  lap is slower than the target by more than `running.interval_pace_tolerance_pct`, default 3 %;
  running faster is always ok). HR is a safety ceiling only (v2): `target_hr_low` = the "passed
  easily" line, `target_hr_high` = the ceiling of the planned session type (VO2max / 5k: 102 % /
  108 % of LTHR, threshold: 95 % / 99 %), `hr_ok` = the lap's peak HR (max, else average) at or
  below the ceiling. Laps of
  easy / long runs get the easy HR range in force for that run, not today's (`hr_ok` = avg HR at or
  below that ceiling) and no
  pace target. Everything else is `null`.
- `verdict.passed`: interval runs -> the engine's `evaluate_intervals` (by pace: >= 80 % of the
  reps on pace, a fade of at most 3 %, all prescribed reps; HR above the ceiling only turns the
  summary into "on pace, but HR above the ceiling"); easy / long -> `evaluate_easy_run` (avg HR
  at or below the ceiling and enough time in Garmin Z1-Z3, or under the ceiling by laps when
  Garmin has no zone seconds; `pct_at_or_below_cap` holds that share; the day after a lower-body session an HR
  miss is a note and `passed` stays true); `null` when there is nothing to judge (non-running
  activity, interval title without lap structure). `reasons` are the engine's messages; for a
  clean run they are short positive facts instead.
  If the average work pace is more than `running.interval_fast_flag_pct` (default 5 %) faster than
  the target, a reason such as `Reps ran ~6% faster than target 4:30/km; the target will catch up
  via VDOT` is added; it never changes `passed` or `pace_ok`. Both keys live in the engine config
  (new config version; code defaults apply to older versions). Note an interval run can pass with a
  "slightly below the target zone" reason: only too-high HR fails.

## GET /stats/health?days=90

`days` consecutive days ending today, **oldest first**, a row for every day (all-`null` fields and
readiness `unknown` when Garmin has nothing).

```json
{
  "generated_at": "2026-10-25T11:24:36.118Z",
  "days": [
    {
      "date": "2026-10-25",
      "hrv_ms": 66.0,
      "hrv_baseline_ms": 70.0,
      "rhr": 53.0,
      "rhr_baseline": 54.0,
      "sleep_s": 26640,
      "sleep_score": 84.0,
      "body_battery_high": 96.0,
      "body_battery_low": 27.0,
      "stress_avg": 12.0,
      "steps": 35,
      "steps_7d_avg": 10871.0,
      "readiness": {
        "level": "green",
        "reason": "all signals normal: HRV 66 ms (-6% vs 70 baseline), resting HR 53 (+1 vs 52), sleep 7.4 h, score 84"
      }
    }
  ],
  "bodyweight": [
    {
      "date": "2026-08-17",
      "weight_kg": 82.0,
      "source": "garmin"
    },
    {
      "date": "2026-10-24",
      "weight_kg": 82.0,
      "source": "manual"
    }
  ],
  "summary": {
    "hrv_7d": 83.0,
    "hrv_28d": 68.3,
    "rhr_7d": 53.4,
    "rhr_28d": 54.4,
    "sleep_7d_s": 30320.0,
    "sleep_28d_s": 29122.0,
    "steps_7d_avg": 10871.0,
    "days_10k_last_7": 4,
    "bodyweight_latest": 82.0,
    "bodyweight_change_4w_pct": null
  },
  "readiness_today": {
    "level": "green",
    "headline": "Ready",
    "metrics": [
      {"key": "hrv", "label": "HRV", "value": 66.0, "unit": "ms", "baseline": 70.0, "delta_pct": -5.7, "status": "ok"},
      {"key": "rhr", "label": "Resting HR", "value": 53.0, "unit": "bpm", "baseline": 52.0, "delta_pct": 1.9, "status": "ok"},
      {"key": "sleep", "label": "Sleep", "value": 7.4, "unit": "h", "baseline": 7.6, "delta_pct": -2.6, "status": "ok"}
    ],
    "details": [
      "All signals normal",
      "HRV 66 ms (-6% vs 70 baseline) - neutral",
      "Resting HR 53 (+1 vs 52) - neutral",
      "Sleep 7.4 h, score 84 (usual 7.6 h) - neutral"
    ]
  }
}
```

- `readiness`: computed by the engine (`score_day`) per day with its normal 7-day baselines;
  `level` is `green` | `yellow` | `red` | `unknown` (no data), `reason` is the engine's sentence.
- `hrv_baseline_ms` / `rhr_baseline`: the mean of the previous 7 days the engine compares with
  (HRV falls back to Garmin's weekly average when fewer than 3 days exist).
- `body_battery_high/low`: the day's max / min body battery.
- `steps_7d_avg`: mean of the days with step data among the 7 days ending that day (rounded to
  whole steps), `null` if none of them has any.
- `bodyweight`: weigh-ins in the window (manual, Garmin, ...), oldest first.
- `summary`: `*_7d` / `*_28d` = mean of the days with data in the last 7 / 28 days (ending today),
  `null` if none. `bodyweight_latest`: latest weigh-in ever. `bodyweight_change_4w_pct`: latest vs
  the weigh-in closest to 28 days before it, accepted only within 14 days of that mark (else
  `null`), in %. `steps_7d_avg`: the same 7-day mean for the last 7 days; `days_10k_last_7`: days
  among them with at least 10 000 steps (`null` when none has step data).
- `readiness_today`: today's readiness as a compact card (also returned by `GET /plan/today`, see
  below). Same engine score as `days[-1].readiness`.
  - `level`: `green` | `yellow` | `red` | `unknown`. `headline`: short text for it, `"Ready"`,
    `"Take it easier"`, `"Recover"` or `"No data"`.
  - `metrics`: one entry per signal the engine could score today, in the order `hrv`, `rhr`,
    `sleep` (empty with no data): `key`, `label`, `value` and `unit` (`ms`, `bpm`, `h`),
    `baseline` (the mean of the previous 7 days the engine compares with; for sleep the mean
    sleep hours of the previous 7 nights, `null` with fewer than 3), `delta_pct` (value vs
    baseline, `null` without baseline) and `status`: `ok` | `warn` | `bad` (the signal's green /
    yellow / red).
  - `details`: the Why list, one line per item: first (only when nothing is flagged) `"All
    signals normal"`, then **one line for every factor the score uses** (`HRV`, `Resting HR`,
    `Sleep`), each with its value vs the personal baseline and its effect on the score, ending in
    `- pulls it down` (red), `- pulls it down a bit` (yellow) or `- neutral` (green; a factor
    never raises the score). Order: decisive factor first (red, then yellow, then neutral; ties
    in the order hrv, rhr, sleep), a factor with no data that day last as `"<Label>: no data"`.
    Sleep lines add `(usual 8.3 h)` when the 7-night baseline is known. The engine's note
    (`"Only a minor flag, so no change"`, `"Short night alone: yellow"`) closes the list. With no
    data at all: `["No data"]`. (`days[].readiness.reason` is unchanged: flagged signals only.)

## GET /stats/correlations?days=120

Five items, always present and in this order (empty `points` when there is no data). `category`
is `recovery` (how the body's state goes with training performance) or `hybrid` (running vs
lifting interference, relevant for an athlete who does both).

| `id` | `category` | x | y |
|---|---|---|---|
| `sleep_vs_lifting` | recovery | hours slept the night before the session | lifting performance (below) |
| `hrv_vs_lift_performance` | recovery | that morning's overnight HRV (ms) | lifting performance on that day |
| `hrv_vs_easy_efficiency` | recovery | overnight HRV (ms) on the day | efficiency (m/min per bpm) of the day's easy run |
| `rhr_vs_easy_efficiency` | recovery | resting HR (bpm) on the day | efficiency of the day's easy run |
| `run_load_vs_lift_performance` | hybrid | running km in the 48 h before the first lifting session of the day (0 if none) | lifting performance on that day |

*Lifting performance* (the same metric in all three lifting items): each lift's best e1RM that
day as % of that lift's best in the previous 8 weeks (100 = matched it, above = PR), averaged over
the day's lifts. A lift's first appearance has no history and is skipped.

The old `sleep_score_vs_readiness` item was removed: readiness already includes sleep, so it
was trivially correlated.

```json
{
  "generated_at": "2026-10-25T11:24:36.145Z",
  "items": [
    {
      "id": "hrv_vs_easy_efficiency",
      "category": "recovery",
      "title": "HRV vs easy-run efficiency",
      "x_label": "Overnight HRV (ms)",
      "y_label": "Efficiency (m/min per bpm)",
      "points": [
        {
          "date": "2026-08-31",
          "x": 104.0,
          "y": 1.2948
        },
        {
          "date": "2026-09-14",
          "x": 86.0,
          "y": 1.3198
        }
      ],
      "r": 0.661,
      "n": 6,
      "note": "n=6, strong positive, not conclusive. Easy runs only; efficiency is speed per heartbeat, so pace differences cancel out.",
      "insight": "Higher HRV tends to go with more efficient easy runs (r=0.66, n=6, early read)"
    }
  ]
}
```

- `r`: Pearson correlation, `null` when `n` < 5 (or one axis is constant). `n` = number of points.
- `note`: plain English, e.g. `n=7, weak positive, not conclusive` (strength from |r|: <0.2
  negligible, <0.4 weak, <0.6 moderate, else strong; "not conclusive" below 30 points), followed by a
  caveat. Show it next to the chart. Garmin files a night's sleep (and HRV / resting HR) under the
  day you woke up, so "sleep that day" is the previous night.
- `insight`: one cautious sentence generated from `r` and `n`, for the card headline:
  - `n` < 5 or `r` null: `Not enough data yet (n=4)`.
  - |r| < 0.2: `No clear link between sleep and lifting performance (r=0.05, n=31)`.
  - 0.2 <= |r| < 0.4: `Weak hint that more sleep goes with better lifting days (r=0.25, n=31)`.
  - |r| >= 0.4: `More sleep tends to go with better lifting days (r=0.42, n=31)`.
  - `n` < 15 adds `, early read` inside the brackets. Correlation is never presented as cause.
- `run_load_vs_lift_performance` is the interference effect: a negative `r` would mean lifting
  days are weaker after more running. Runs count when they start up to 48 h before the session
  start (running activities only, deleted ones excluded).

## GET /plan/today: `readiness_today`

`GET /api/v1/plan/today` (see [sync.md](sync.md#plan-json-plantoday-planweek-and-planned_sessions-in-pull))
carries the same `readiness_today` object as `/stats/health` (additive; the existing `readiness`
object is unchanged), so the Today screen needs no second request. Example:
`examples/plan_today.json` (a day without Garmin data: level `unknown`, headline `No data`).
