# Sync + catalog API (step 4 contract)

Everything the iOS app needs to log workouts offline and stay in sync with the server.
Base URL `https://<host>/api/v1`, JSON only.

- Auth: `Authorization: Bearer <API_TOKEN>` on every `/api/v1/*` endpoint except
  `GET /api/v1/health` (the legacy `GET /health` is public too).
- Prefixes: every path in the API docs is also served under `/api/v1/fitness/...` (e.g.
  `/api/v1/fitness/sync/pull`) with the same behaviour and auth; the app keeps using `/api/v1/...`
  until it is switched. Only `GET /health` has no `/api/v1` form.
- Conventions: snake_case keys; ids are **lowercase** UUID strings (Swift's `UUID().uuidString` is
  uppercase: lowercase it before sending; uppercase input is tolerated but the response echoes the
  lowercase form); weights in kg; catalog ids (`variant_id`, `gym_id`, ...) are integers.
- Timestamps: ISO-8601 UTC with **exactly three fractional digits** and `Z`, e.g.
  `2026-10-26T16:30:12.000Z` (Swift's plain `.iso8601` decoding strategy rejects fractional
  seconds: use `ISO8601DateFormatter` with `.withFractionalSeconds`). The server accepts input with
  or without fractional seconds, and naive timestamps are read as UTC. `date` fields are
  `yyyy-MM-dd` (local day in the user's timezone).
- Real example payloads produced by the real code: [`examples/`](examples/) (see the end).

## Endpoints

| Method | Path | Auth | Purpose |
|---|---|---|---|
| GET | `/api/v1/health` | no | connectivity check |
| POST | `/api/v1/sync/push` | yes | upload sessions / sets / body weights (last-write-wins) |
| GET | `/api/v1/sync/pull?since=<cursor>` | yes | changes + catalog + plan + config |
| GET | `/api/v1/variants/{id}/history?limit=10` | yes | previous performance of a variant |
| GET | `/api/v1/plan/today`, `/api/v1/plan/week?start=` | yes | plan (existing, see below) |
| POST | `/api/v1/engine/deload/cancel` | yes | cancel the lifting deload of a week ([engine.md](engine.md)) |

### GET /api/v1/health

```json
{"status": "ok", "version": "0.1.0"}
```

### POST /api/v1/sync/push

Request (`examples/sync_push_request.json`):

```json
{
  "device_id": "iphone-demo",
  "sessions": [
    {"id": "3b17c1ba-cddb-5750-9ffd-d5b446846003",
     "planned_session_id": "c6eaf326-5d7b-5713-bec2-e1db59b16e83",
     "gym_id": 4, "kind": "upper", "name": "Upper",
     "started_at": "2026-10-26T16:30:12.000Z", "ended_at": "2026-10-26T17:24:40.000Z",
     "notes": null, "updated_at": "2026-10-26T17:24:41.000Z", "deleted_at": null}
  ],
  "sets": [
    {"id": "eca0168f-be5a-514e-a28c-5414a6c16358",
     "session_id": "3b17c1ba-cddb-5750-9ffd-d5b446846003",
     "variant_id": 42, "exercise_order": 0, "set_order": 1, "set_type": "working",
     "weight_kg": 17.5, "reps": 7, "duration_s": null,
     "completed_at": "2026-10-26T16:35:30.000Z", "notes": null,
     "updated_at": "2026-10-26T16:35:31.000Z", "deleted_at": null}
  ],
  "body_measurements": [
    {"id": "89cc74cd-4696-5d65-a5e4-e4fb372f5b7a", "measured_at": "2026-10-26T06:45:00.000Z",
     "weight_kg": 82.4, "note": null,
     "updated_at": "2026-10-26T06:45:02.000Z", "deleted_at": null}
  ]
}
```

| Record | Fields |
|---|---|
| Session | `id`, `planned_session_id\|null`, `gym_id\|null`, `kind` (`upper\|lower\|full\|other`), `name`, `started_at`, `ended_at\|null`, `notes\|null`, `updated_at`, `deleted_at\|null` |
| Set | `id`, `session_id`, `variant_id`, `exercise_order` (>=0, block position in the session), `set_order` (>=0, position in the block), `set_type` (`working\|warmup\|drop\|failure`), `weight_kg\|null` (0-1000), `reps\|null` (0-1000), `duration_s\|null`, `completed_at\|null`, `notes\|null`, `updated_at`, `deleted_at\|null` |
| BodyMeasurement | `id`, `measured_at`, `weight_kg` (20 < w < 400), `note\|null`, `updated_at`, `deleted_at\|null` |

Response (`examples/sync_push_response.json`), one result per pushed record, in request order
(sessions, then sets, then body measurements):

```json
{"server_time": "2026-10-25T07:38:59.214Z",
 "results": [
   {"entity": "session", "id": "3b17c1ba-...", "status": "applied", "reason": null},
   {"entity": "set", "id": "eca0168f-...", "status": "applied", "reason": null},
   {"entity": "set", "id": "0b6f...", "status": "rejected", "reason": "unknown session_id 5d3c..."}
 ]}
```

Semantics

- **Per-record last-write-wins on `updated_at`**: upsert by `id`; the incoming record is applied
  only if its `updated_at` is strictly newer than the stored one. Equal or older -> `stale`
  (nothing changes; keep the server version, you will get it with the next pull). The client sets
  `updated_at` on every local edit (and on a delete).
- **Idempotent**: re-pushing a payload changes nothing (every record answers `stale`). Treat
  `applied` and `stale` both as "the server has dealt with it, drop it from the outbox".
- `rejected` = this record is invalid and will never be accepted as sent (reason in `reason`):
  unknown `session_id` (the session is neither in the payload nor on the server), unknown
  `variant_id` / `gym_id`, `ended_at` before `started_at`, schema/range errors
  (`invalid: field: message`). One rejected record never fails the request or the other records.
  The HTTP status is 200 unless the envelope itself is malformed (422) or auth fails (401).
- Sets may be sent before or together with their session (the server applies sessions first).
- **Deletes are tombstones**: push the record again with `deleted_at` set and a newer
  `updated_at`. Rows are never hard-deleted. A newer record with `deleted_at: null` resurrects.
  Deleting a session does not cascade: send tombstones for its sets as well.
- Whole push = one transaction. Safe to retry on network errors.
- `planned_session_id` that the server does not know is silently dropped (stored as null), not
  rejected. Rows from the app are stored with `source = app` (`device_id` is kept on sessions).
- Side effects after a push that applied session/set changes:
  1. the linked planned session turns `done` (kind matches: upper session on an upper plan,
     `full` counts for both) or `modified`; if its last linked session is deleted again it goes
     back to `proposed`. The change reaches you through the next pull (`planned_sessions`);
  2. `exercise_session_stats` (e1RM, PRs, volume) are recomputed for the touched variants (all
     variants if a body weight was pushed);
  3. the training engine re-runs in the background **after** the response (reconcile + re-plan this
     and next week). Its failure is logged and never fails the push. Pull a bit later (or on the
     next app open) to see the updated plan.

### GET /api/v1/sync/pull?since=\<cursor\>

`since` empty (or omitted) = **snapshot**: the whole catalog, plus training history limited to
the last 120 days of sessions (and their sets), all body measurements, planned sessions from the
window start on. Old history is served by stats endpoints later. Otherwise `since` is the
`cursor` of the previous response: everything written since then, **including tombstones**.
Optional `limit` (1-5000, default 2000) = page size, for tests.

```
GET /api/v1/sync/pull?since=            -> {"cursor": "c9469", "has_more": false, ...}
GET /api/v1/sync/pull?since=c9469       -> {"cursor": "c9480", "has_more": false, ...}
```

Response (`examples/sync_pull.json`, `examples/sync_pull_incremental.json`):

```json
{
  "cursor": "c9469", "has_more": false, "server_time": "2026-10-25T07:38:58.973Z",
  "sessions": [Session...], "sets": [Set...], "body_measurements": [BodyMeasurement...],
  "planned_sessions": [PlannedSession...],
  "variants": [Variant...], "exercises": [Exercise...], "gyms": [Gym...],
  "config": {
    "version": "v3.1", "timezone": "Europe/Vienna",
    "dumbbell_increment_kg": 2.0, "rep_range": [7, 12], "rir_target": [0, 1],
    "increments_kg": {"barbell": 5.0, "smith": 5.0, "plate_loaded": 5.0, "dumbbell": 2.0,
                      "default_stack_step": 5.0, "bodyweight_added": 2.5, "other": 2.5},
    "lifting": {"rest_default_s": 120, "rest_s_by_variant": {"122": 180}}
  }
}
```

- `config.lifting` (rest timers, `docs/api/exercises.md`): `rest_default_s` is the default rest and
  `rest_s_by_variant` lists the per-exercise overrides under every live variant id of that exercise
  (JSON keys are strings). It is in every pull, so the app reconciles its local overrides with it.

- `sessions`, `sets`, `body_measurements` have exactly the push shapes (`updated_at` /
  `deleted_at` included). For rows the server wrote itself (Strong import) `name` may be `""`
  and `ended_at` may be null.
- `Variant`: `{"id": 122, "exercise_id": 2, "name": "Incline Bench Press (Smith Machine) @ Gym 3",
  "gym_id": 4, "equipment_type": "smith", "load_convention": "total", "active": true,
  "deleted_at": null, "weight_ladder": [..]|null, "rep_range": [7, 12]|null,
  "increment_kg": 2.5|null}`. `equipment_type`:
  `barbell|dumbbell|machine_stack|plate_loaded|cable|bodyweight|smith|other`;
  `load_convention`: `total` (logged weight is the whole load), `per_hand` (one dumbbell),
  `assistance` (assisted pull-up: lower = harder; for band variants the number is a band level).
  `weight_ladder` = available weights for stack machines / dumbbells, ascending, or null.
  `rep_range` = per-exercise override from the program, null = use `config.rep_range`.
  Inactive variants (no longer trained) are included with `active: false`.
  `deleted_at` is a tombstone: the importer soft-deletes variants nothing refers to any more
  (no sets, no name mapping); remove them locally / hide them from pickers. A tombstoned variant
  can come back (`deleted_at: null` again, same `id`) if it is needed later. Pushing a set for a
  tombstoned variant is still accepted.
- `Exercise`: `{"id": 1, "name": "Bench Press", "muscle_group": "chest", "movement_pattern":
  "horizontal_push", "body_region": "Chest", "default_equipment": "barbell", "is_timed": false,
  "progression": "load"}`. `progression` is `load | reps | time` (`reps`: the plan never adds weight,
  see `docs/api/exercises.md`); `is_timed` is kept and always equals `progression == "time"`.
  Exercises the user made in the app (`docs/api/exercises.md`) come the same way.
  `increment_kg` on a variant is the user's load step (null = ladder / equipment default).
- `Gym`: `{"id": 4, "name": "Gym 3", "deleted_at": null}`. `deleted_at` is an addition to the base
  contract: retired gyms (e.g. "Main gym") are still sent so old data resolves, hide them in
  pickers when `deleted_at != null`.
- `PlannedSession` = the item shape of `/plan/week` (below) plus `deleted_at`; `id` is stable
  (one per week and template slot), `status` is `proposed|approved|done|skipped|modified`. The
  server regenerates plans, so a planned session can change between pulls (same `id`, newer
  `updated_at`): upsert by id.
- `kind` of pulled sessions is always one of `upper|lower|full|other` (Strong history knows finer
  kinds: `push`/`pull` are served as `upper`, `legs` as `lower`, `calisthenics` and the rest as
  `other`).
- `variants` / `exercises` / `gyms` come in full on a snapshot and as changes afterwards
  (usually none). Records inside a page are **not ordered by dependency** (a set can precede its
  session): apply a whole page in one local transaction and do not enforce foreign keys while
  doing so.

**Cursor and paging.** The cursor is opaque (do not parse it, do not compare it); store the
`cursor` of the last fully processed page and send it back as `since`. While `has_more` is true,
pull again with the returned cursor immediately; only store the cursor as your resume point once
the page is applied. A snapshot can span several pages (page size ~2000 records, merged over all
tables in change order); the 120-day filter is carried in the cursor, so it also holds on page 2+,
and the cursor you get on the last snapshot page continues cleanly with incremental changes.
An unparsable cursor answers `400`; recover with an empty `since` (a full re-snapshot).

**Server-side change sequence.** The cursor is based on `server_seq`, a server counter stamped by
database triggers on every insert / change of these tables, whatever wrote it (app push, Strong
import, engine, scripts). It is independent of the client-supplied `updated_at`, which is only
used for last-write-wins. Derived-only changes (`e1rm_kg` recomputation) are not changes.

**Echo.** Your own pushed records come back in the next pull (with the same `updated_at`);
upserting them is a no-op. If you hold a newer local edit that is still in the outbox, do not
overwrite it with an older server copy: compare `updated_at` (same LWW rule as the server).

### GET /api/v1/variants/{id}/history?limit=10

Last N sessions (1-100, default 10, newest first) that contain the variant, for the "previous
performance" inline display. Tombstoned sessions / sets excluded. Unknown variant: 404.
`best_e1rm` is the session's best estimated 1RM in kg (Epley; bodyweight lifts use body weight +
added weight; null when not meaningful). All set types are listed in set order; filter on
`set_type` as needed. `examples/variant_history.json`:

```json
[{"session_id": "3b17c1ba-cddb-5750-9ffd-d5b446846003", "date": "2026-10-26",
  "sets": [{"weight_kg": 17.5, "reps": 7, "duration_s": null, "set_type": "working"},
           {"weight_kg": 17.5, "reps": 7, "duration_s": null, "set_type": "working"}],
  "best_e1rm": 114.58}]
```

## Plan JSON (`/plan/today`, `/plan/week`, and `planned_sessions` in pull)

`GET /api/v1/plan/today` (`examples/plan_today.json`) =
`{"date", "readiness": {...}, "readiness_today": {...}, "sessions": [PlanItem...]}`
(`readiness_today` is the compact card also in `/stats/health`, see [stats.md](stats.md);
`readiness` is the engine's full score);
`GET /api/v1/plan/week?start=<any date>` (`examples/plan_week.json`) =
`{"start": "2026-10-26", "days": [{"date", "sessions": [PlanItem...]}] x7}`. Days with nothing
stored get a computed item with `"id": null, "status": "preview"` (not in `planned_sessions`).

A lifting item, abridged (taken from the real engine output in `examples/plan_week.json`; this
week is the soft deload that replaced the v1 engine's planned -10 % resets):

```json
{
  "id": "c6eaf326-5d7b-5713-bec2-e1db59b16e83", "date": "2026-10-26",
  "kind": "upper", "status": "proposed", "adaptive": true,
  "adapts_on": ["your reps at each load", "today's readiness",
                "censored misses (readiness, hard runs, breaks)", "stall ladder and deload weeks"],
  "moved_from": null, "change_reason": null, "title": "Upper (deload)",
  "slot_id": "mon_upper", "template_kind": "upper",
  "readiness": {"color": "green", "known": false, "reason": "no data"},
  "adjustments": [], "notes": [], "config_version_id": 8,
  "updated_at": "2026-10-25T15:45:23.767Z",
  "session": {
    "type": "lifting", "kind": "upper", "gym": "Gym 3",
    "deload": {"active": true, "from": "2026-10-26", "until": "2026-11-01",
               "reason": "legacy_reset"},
    "exercises": [
      {"order": 2, "variant_id": 122, "variant_key": "634a70a4-b4fd-5a14-a86a-429cf78ff39e",
       "name": "Incline Bench Press (Smith Machine) @ Gym 3", "exercise": "incline_bench_press",
       "equipment_type": "smith", "load_convention": "total", "rep_range": [7, 12],
       "effective_rep_range": [7, 12], "progression_state": "deload",
       "progression_note": "deload week (several lifts stalled; replaces the planned -10% reset): 2 of 3 sets, same load; retest 80 kg after the deload",
       "rest_s": 120,
       "sets": [{"index": 1, "set_type": "working", "target_weight": 80.0, "target_reps": 9},
                {"index": 2, "set_type": "working", "target_weight": 80.0, "target_reps": 8}],
       "last": {"date": "2026-10-13", "best_e1rm": 104.0,
                "sets": [{"weight": 80.0, "reps": 9, "duration_s": null},
                         {"weight": 80.0, "reps": 8, "duration_s": null},
                         {"weight": 80.0, "reps": 7, "duration_s": null}]},
       "rule": "deload:legacy_reset", "reason": "deload week (...) (2026-10-26 .. 2026-11-01)",
       "flags": ["deload", "retest_pending"]}
    ]
  }
}
```

The Friday quality session of the same week, abridged:

```json
{"type": "intervals", "total_km": 5.2, "phase": "threshold", "phase_week": 1,
 "progression_note": "first threshold session of the cycle",
 "structure": {
   "session_type": "threshold",
   "warmup": {"distance_km": 1.0, "hr": {"zone": "Z1-Z2", "low": 127, "high": 156, "basis": "lthr"}},
   "work": {"reps": 3, "distance_m": 1000.0, "duration_s": 294, "pace_s_per_km": 294,
            "pace_label": "4:54", "pace_range_s_per_km": [290.0, 298.0], "pace_tolerance_pct": 3.0,
            "hr": {"zone": "Z4", "low": 166, "high": 173,
                   "note": "pace leads; HR is a ceiling only (wrist HR lags on short reps)"}},
   "recovery": {"distance_m": 240, "duration_s": 90.0, "type": "jog", "hr_drop_min_bpm": 12.0},
   "cooldown": {"distance_km": 0.7}},
 "progression": {"rule": "quality:start", "reason": "...", "lever": null, "vdot": 43.44,
                 "last_session": null}}
```

- Additive fields on **every** plan item (stored and preview, `/plan/today`, `/plan/week` and
  `planned_sessions` in pull):
  - `status`: `proposed|approved|done|skipped|modified` (`preview` for computed items);
  - `adaptive` (bool): `true` while the session is still open (`proposed`/`approved`/`preview`) and not
    a rest day: its content is recomputed on every engine run; `false` for done / skipped sessions and
    rest;
  - `adapts_on` (`[str]`, empty when not adaptive): short phrases for the app (free text, may change):
    `["pace of your last quality session", "training phase (quality cycle)", "VDOT updates",
    "today's readiness", "weekly volume cap", "recovery after lower-body days"]` (intervals),
    `["your longest recent long run", "HR drift of the last long run", "step-back weeks", "your recent
    easy-run paces", "today's readiness", "weekly and single-run volume caps"]` (long run), `["your
    recent easy-run paces", "today's readiness", "weekly and single-run volume caps"]` (easy run), `["your reps at each load", "today's readiness", "censored
    misses (readiness, hard runs, breaks)", "stall ladder and deload weeks"]` (upper / lower);
  - `moved_from` (`YYYY-MM-DD|null`): the original date if the session was moved or is a make-up;
  - `change_reason` (`str|null`): why, e.g. "Lower missed Tue → moved to Wed, replacing the easy run"
    (engine make-up), a recovery swap ("intervals on ... too soon after the lower-body day ...") or
    the reason of an approved coach change. Who changed it is not exposed.
- `kind`: `upper|lower|easy_run|intervals|long_run|rest`. `session.type` is `lifting`, `easy_run`,
  `long_run`, `intervals` or `rest` (see `examples/plan_week.json` for run / rest shapes).
- Lifting exercises: `rest_s` (int seconds, 30..600) is the resolved rest timer of the exercise:
  `exercise_overrides.<slug>.rest_s` if set, else `lifting.rest_default_s` (120). It is on every
  lifting exercise of every plan item (also `unresolved` ones) and resolved when the plan is read, so
  a changed rest time shows in sessions that are already planned. `PUT /exercises/{id}/rest` bumps
  open lifting sessions, so an incremental pull re-sends them. Older servers omit the field (the app
  falls back to 120).
- Lifting exercises: `variant_id` links to the catalog (match with `variants`). An exercise the
  engine cannot resolve yet has `"unresolved": true`, no `variant_id` and `target_weight: null`.
  Timed exercises carry `target_duration_s` instead of reps. `target_weight` is in the variant's
  logged convention (per dumbbell for `per_hand`).
- When the user logs a session against a plan item, set `planned_session_id` on the pushed session.
- **Progression engine v2 (additive, optional; absent on plans made by older engines).** Rules and
  sources: [decisions.md](../spec/decisions.md) "Progression engine v2".
  - Lifting exercises: `effective_rep_range` (`[low, high]`: the rep range in force at this load;
    the top may be raised up to 15 when the next load step is too big a jump, the "rung rule"),
    `progression_state` (`progressing | grinding | stalled | deload | retest | rebuild`) and
    `progression_note` (one short human sentence, e.g. "+1 rep per set: beat 10/9/8 @ 60 kg").
    `rule` / `flags` gain new values (`double_progression:hold_noise`, `stall:reset_small`,
    `stall:advisor`, `retest`, `deload:<reason>`, `return_after_break`, `rung_infeasible`; flags
    `ceiling_raised`, `rung_infeasible`, `retest_pending`, `retest`, `break`, ...): display only.
  - Lifting session: `deload` (`{active, from, until}` as before) gains `reason`
    (`multi_lift_stall | fatigue_index | red_streak | legacy_reset`). A deload keeps the loads and
    halves the sets (rounded up).
  - Run sessions (`easy_run`, `intervals`, `long_run`): `phase` (`threshold | threshold_vo2max |
    5k_specific | time_trial`), `phase_week` (int, 1-based) and `progression_note` (str).
  - Intervals: `structure.session_type` (`threshold | vo2max | race_5k | time_trial`),
    `structure.work.duration_s` (approximate seconds per rep), `structure.work.pace_tolerance_pct`,
    `structure.work.hr.note`, `structure.recovery.duration_s` (seconds, null for distance-based
    recoveries; `distance_m` stays, approximated from the duration for threshold reps). For a
    `time_trial` `structure.recovery` is `null`. `progression` gains `lever` (`reps | rep_length |
    recovery | pace | null`) and `vdot`; `progression.last_session` carries the pace-based
    evaluation (`verdict`: `green | pass | pass_hard | fail | no_target`, `reps_on_pace`,
    `pace_ok_share`, `last_rep_peak_hr`, `hr_safety_exceeded`, ...).
  - Easy runs: `structure` = `{"strides": {"reps", "duration_s", "recovery_s", "note"}}` (the
    `structure` key was intervals-only before; its `work` / `recovery` are absent here).
    `last_easy_run` also carries `hr_ceiling`, the ceiling that run was judged by (the zones in
    force when it was run). `hr.zone` / `low` / `high` are the engine's current zones: with Garmin's
    floors easy is `Z2-Z3` (121-150 at the %HRR floors, `basis: "garmin"`), without them `Z1-Z2`;
    threshold `Z4` and VO2max / 5k `Z4-Z5a` always come from LTHR, as in the example above.
    `last_easy_run` gains `exempt` (bool) and `notes` (`[str]`): the day after a lower-body
    session an HR fail is shown as a note, not a fail.
  - Long runs: `step_back` (bool) and `progression.drift` (`hold | cut | null`).
- **Easy pace calibration (additive, optional; absent on older plans).** Easy and long runs:
  `pace` gains `basis` (`calibrated | daniels`; `model` for a non-VDOT pace model) and `reason`
  (one sentence, e.g. "Easy pace from your 4 easy runs of 28 days at HR <= 154 (5:11-5:36/km,
  median 5:21): easy VDOT 44.8 vs 42.0 for quality sessions"). `min_s_per_km` / `max_s_per_km` /
  `label` keep their meaning; with `calibrated` they come from the athlete's own easy runs instead
  of the quality VDOT. Interval paces are unchanged. Rules: [decisions.md](../spec/decisions.md)
  "Easy pace calibration (2026-10-28)".

## Errors

| Status | Meaning |
|---|---|
| 200 | processed (inspect `results[].status`) |
| 400 | pull: malformed cursor |
| 401 | missing / wrong bearer token |
| 404 | history: unknown variant |
| 422 | malformed envelope (e.g. missing `device_id`, wrong JSON type) |
| 503 | server has no `API_TOKEN` configured |

## Deviations from / additions to the agreed contract

All additive unless noted:

1. Timestamps are emitted with fixed millisecond precision (see top); pushes are accepted either way.
2. Pull response carries `server_time` too; `Variant` has `deleted_at` (see above); `config` has extra keys (`timezone`, `rir_target`,
   `increments_kg`); `Gym` has `deleted_at`; pull accepts an optional `limit`.
3. Pulled `kind` is mapped to the four app kinds (see above); pushed kinds outside the four are
   rejected as invalid.
4. A re-push of an unchanged record answers `stale` (rule "equal -> keep server"), not `applied`.
5. `ended_at` is stored as a duration in whole seconds (`ended_at - started_at`, rounded), so it
   comes back with sub-second precision dropped.
6. Pushed body measurements are stored with `source = manual` (the DB enum has no `app`).
7. An unknown `planned_session_id` is dropped instead of rejecting the session.
8. The engine re-run is a background task after the HTTP response (so plan changes show up in a
   later pull, not in the immediate one).
9. Rest timers (additive): `rest_s` on lifting plan exercises and `config.lifting` in pull;
   endpoint in `docs/api/exercises.md`.
10. Progression engine v2 (additive): the plan fields listed under "Plan JSON". Nothing was
    removed or renamed; old plans simply lack them.
11. User-managed exercises (additive): `body_region`, `default_equipment`, `is_timed` on
    `Exercise`, `increment_kg` on `Variant`; endpoints in `docs/api/exercises.md`.

12. Progression mode (additive): `progression` on `Exercise` (`load | reps | time`), optional
    `progression` on `POST /exercises` and `PATCH /exercises/{id}`. `is_timed` is unchanged.

## Example fixtures

Produced by `backend/apps/fitness/scripts/gen_api_examples.py` (real endpoints via FastAPI `TestClient` against
a copy of the real database, with "today" pinned to Monday 2026-10-26; the current engine is run on
the copy first, so the plans show its output; the live database is not touched):
`plan_today.json`, `plan_week.json`, `sync_pull.json` (snapshot; sessions trimmed to the newest 3,
a `_note` key says so), `sync_pull_incremental.json` (changes caused by the example push),
`sync_push_request.json`, `sync_push_response.json`, `variant_history.json`.
Regenerate: `cd backend && uv run python -m apps.fitness.scripts.gen_api_examples`.
