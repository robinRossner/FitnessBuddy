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
      "gym_id": 4,
      "gym_name": "Gym 3",
      "active": true,
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
- `running_*`: running activities only (hiking etc. excluded), by local start day.

## GET /stats/running?weeks=26

```json
{
  "generated_at": "2026-10-25T11:24:36.071Z",
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
      }
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

- `weekly.time_in_zones_s`: seconds in HR zones **z1..z5**, using the zones of the *active engine
  config* (derived from LTHR, never stored). The engine has Friel's 7 running zones
  (`z1, z2, z3, z4, z5a, z5b, z5c`); they are collapsed into five:

  | API zone | Friel zones | at LTHR 171 |
  |---|---|---|
  | `z1` | z1 | up to 145 bpm |
  | `z2` | z2 | 146-152 (top = easy-run ceiling) |
  | `z3` | z3 | 153-161 |
  | `z4` | z4 | 162-169 |
  | `z5` | z5a + z5b + z5c | 170 and up |

  The seconds come from Garmin's per-second zone data, re-binned onto these zones (Garmin's own
  zone floors differ; time inside one Garmin zone is assumed evenly spread over its bpm range).
  Runs without Garmin zone data use their laps: each lap's duration goes to the zone of its
  average HR. Runs with neither count as 0.
- `efficiency` (easy and long runs, oldest first): `efficiency` = **metres per minute per bpm**
  = (distance_m / (duration_s / 60)) / avg_hr; higher = fitter. `decoupling_pct` = pace:HR drift
  between the first and second half (positive = HR drifted up; `null` under 4 laps with HR).
  `type`: the linked plan's kind, else `intervals` if the title / laps show an interval session,
  else `long` from `running.long_run.min_km` (8 km), else `easy`.
- `vdot`: one point per run day, the engine's VDOT estimate on that day (seed from the config,
  raised by the best hard effort of the last `vdot.lookback_weeks`).
- `intervals`: interval sessions (engine `split_intervals` / `evaluate_intervals`).
  `target_pace_s_per_km`: from the linked planned session, else the pace model's interval pace on
  that day. `target_hr_low/high`: the interval HR zone (z4 to z5a). `hr_in_range`, `recovery_ok`
  (HR drop in the recovery laps vs `intervals.hr_recovery_min_bpm`) may be `null`.
  `passed` is the engine's verdict.

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
      "target_hr_low": 166,
      "target_hr_high": 179,
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
    "summary": "5 reps at 4:10/km, work HR 166: criteria met.",
    "reasons": [
      "work HR 166 below the target zone (166)",
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
  running faster is always ok) and the interval HR zone (`hr_ok`). Laps of
  easy / long runs get the easy HR range (`hr_ok` = avg HR at or below the easy ceiling) and no
  pace target. Everything else is `null`.
- `verdict.passed`: interval runs -> the engine's `evaluate_intervals` (work HR not above the zone,
  recovery drop OK, all prescribed reps); easy / long -> `evaluate_easy_run` (avg HR at or below
  the ceiling and enough time under it); `null` when there is nothing to judge (non-running
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
    "bodyweight_latest": 82.0,
    "bodyweight_change_4w_pct": null
  }
}
```

- `readiness`: computed by the engine (`score_day`) per day with its normal 7-day baselines;
  `level` is `green` | `yellow` | `red` | `unknown` (no data), `reason` is the engine's sentence.
- `hrv_baseline_ms` / `rhr_baseline`: the mean of the previous 7 days the engine compares with
  (HRV falls back to Garmin's weekly average when fewer than 3 days exist).
- `body_battery_high/low`: the day's max / min body battery.
- `bodyweight`: weigh-ins in the window (manual, Garmin, ...), oldest first.
- `summary`: `*_7d` / `*_28d` = mean of the days with data in the last 7 / 28 days (ending today),
  `null` if none. `bodyweight_latest`: latest weigh-in ever. `bodyweight_change_4w_pct`: latest vs
  the weigh-in closest to 28 days before it, accepted only within 14 days of that mark (else
  `null`), in %.

## GET /stats/correlations?days=120

Three items, always present (empty `points` when there is no data):

| `id` | x | y |
|---|---|---|
| `sleep_vs_lifting` | hours slept the night before the session | the session's best e1RM per lift as % of that lift's best in the previous 8 weeks (100 = matched it, above = PR), averaged over the day's lifts |
| `hrv_vs_easy_efficiency` | overnight HRV (ms) on the day | efficiency (m/min per bpm) of the day's easy run |
| `sleep_score_vs_readiness` | sleep score of the night ending that morning | that day's engine readiness as a number (red 0, yellow 1, green 2) |

```json
{
  "generated_at": "2026-10-25T11:24:36.145Z",
  "items": [
    {
      "id": "hrv_vs_easy_efficiency",
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
      "note": "n=6, strong positive, not conclusive. Easy runs only; efficiency is speed per heartbeat, so pace differences cancel out."
    }
  ]
}
```

- `r`: Pearson correlation, `null` when `n` < 5 (or one axis is constant). `n` = number of points.
- `note`: plain English, e.g. `n=7, weak positive, not conclusive` (strength from |r|: <0.2
  negligible, <0.4 weak, <0.6 moderate, else strong; "not conclusive" below 30 points), followed by a
  caveat. Show it next to the chart. Garmin files a night's sleep under the day you woke up,
  so "sleep that day" is the previous night. Readiness already includes sleep, so the third item
  has some built-in correlation.
