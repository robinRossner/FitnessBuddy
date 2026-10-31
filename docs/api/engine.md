# Engine endpoints

Bearer token like the other write APIs (`Authorization: Bearer <token>`). The plan endpoints
(`GET /api/v1/plan/today`, `/plan/week`, `/readiness`, `/config`, `POST /config/rollback/{version}`)
are described in [sync.md](sync.md), [exercises.md](exercises.md) and [coach.md](coach.md).

## POST /api/v1/engine/deload/cancel

Cancel the soft deload (sets halved, loads kept) of one week, e.g. the one a multi-lift stall
created. Body:

```json
{"week_start": "2026-10-26", "reason": "feeling fine"}
```

`week_start` is any date of the week (Monday start); `reason` is optional (`null` / missing) and is
kept in the audit log.

Effect, for the week and for every later engine run (the cancel is a `decisions_log` row of kind
`deload_cancel`, `output.author = "user"`, subject = the week's Monday, which the window loader
honours):

- the lifts that were in the soft deload get their normal set counts;
- the lifts the deload was going to retest (the stalled ones) retest their old loads **in this week**
  with normal sets; their `progression_note` reads "Retest at old load 60 kg: beat 11/10/9";
- every other lift progresses by the normal double-progression rules;
- the running step-back no longer lines up with that deload (`strength.deload.align_running_step_back`);
- the week's plan is regenerated and stored right away (what `engine run` would do), so the app sees
  the new targets on the next pull.

Only the engine's persisted deloads (multi-lift stall, fatigue index, converted v1 resets) can be
cancelled; the reactive deload from a run of red readiness days is recomputed from the readiness
data every day and stays.

Response `200`:

```json
{"week_start": "2026-10-26", "status": "cancelled", "changed": true,
 "message": "Deload of the week of 2026-10-26 cancelled: normal sets; 8 lifts retest their old loads now",
 "reason": "feeling fine", "retest": ["Bench Press (Barbell)", "..."], "replanned": true}
```

Idempotent. `status` is one of `cancelled`, `already_cancelled` (cancelling twice changes nothing)
and `no_deload` (the week has no deload: nothing is written). Both of the latter have
`"changed": false` and a `message` saying so.

Admin CLI with the same effect:

```
uv run python -m apps.fitness.cli engine deload cancel --week 2026-10-26 [--reason "..."] [--no-replan]
```

## POST /api/v1/engine/run

Re-run the engine now, exactly like the re-run after a Garmin sync or an app push (reconcile earlier
plans, handle missed sessions, re-plan and store the current and the next week), then the Garmin
workout push hook (server only, pushes only when the planned runs changed). It runs even when
`ENGINE_AUTO_RUN` is off; two runs never overlap. No body. Idempotent: a second call on unchanged
data stores nothing new (`updated: 0`).

Response `200` (short summary, one entry per planned week):

```json
{"config_version": "v6.4", "reconciled": {"done": 1, "skipped": 0},
 "makeups": [{"kind": "easy_run", "missed": "2026-10-28", "action": "dropped"}],
 "weeks": [
  {"week_start": "2026-10-26",
   "persisted": {"created": 0, "updated": 2, "unchanged": 5},
   "quality_vdot": 44.51,
   "vdot_reason": "VDOT 42.4 -> 45.4 measured from interval reps: 6 x 800 m on 2026-08-26 at 4:22/km (...) (capped at +3 per update)",
   "vdot_from_intervals": {"vdot": 45.8, "reason": "6 x 800 m on 2026-08-26 at 4:22/km (...): VDOT 45.8"},
   "easy_pace": {"basis": "daniels", "vdot": 44.51, "reason": "Daniels easy pace for VDOT 45.1: ..."},
   "runs": [
    {"date": "2026-10-30", "kind": "intervals", "session_type": "threshold", "reps": 3,
     "distance_m": 1000.0, "pace_label": "4:38"},
    {"date": "2026-11-01", "kind": "long_run", "distance_km": 7.0, "pace_label": "5:19-6:22",
     "pace_basis": "daniels"}]}]}
```

`quality_vdot` drives threshold / interval / 5k paces; `easy_pace.vdot` (>= `quality_vdot`) the easy
and long run range (`basis`: `calibrated` from the athlete's easy runs or `daniels`).
`vdot_from_intervals` is the current measured estimate from interval reps (`vdot: null` when no
session qualifies). `persisted` is `null` only for weeks that were not stored. `409` when the program
file is missing (`python -m apps.fitness.cli engine program generate`).

