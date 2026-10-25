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
uv run python -m app.cli engine deload cancel --week 2026-10-26 [--reason "..."] [--no-replan]
```
