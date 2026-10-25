# Coach API (step 6 contract)

The Claude advisor layer. The deterministic engine plans every session and computes every weight,
rep target and pace. Claude is consulted rarely and may propose **parameter changes** (a dotted
config path and a new value, see [the allowlist](#what-claude-may-change)) and **schedule changes**
(move / skip / swap / shorten / replace a planned session of the current or next week, see
[Schedule changes](#schedule-changes)); it never sets a weight or a pace. The app shows its proposals, the user approves or rejects them, and every applied change
becomes a new engine config version (author `advisor`, with its reason) that can be rolled back.

- Base URL `https://<host>/api/v1`, `Authorization: Bearer <API_TOKEN>` on every endpoint (401
  otherwise). snake_case, `date` = `YYYY-MM-DD`, timestamps ISO-8601 UTC with milliseconds and `Z`.
- **All ids are strings** (`Proposal.id`, `Change.id`, `consultation_id`, `latest_pending_id`). Request
  bodies accept ids as strings or numbers. A change id is prefixed by its list: `p<n>` for a parameter
  change (`changes[]`), `s<n>` for a schedule change (`schedule_changes[]`), so ids are unique across
  both lists of a proposal. `approve` accepts both kinds in `change_ids`; a bare number is read as a
  parameter change (`"3"` = `"p3"`) for older clients.
- Real payloads (a copy of the real database, "today" pinned to 2026-10-25, Claude replaced by a
  canned answer) are in [`examples/coach_*.json`](examples/), regenerated with
  `cd backend && uv run python -m scripts.gen_coach_examples`:
  `coach_proposals.json`, `coach_proposal_detail.json`, `coach_ask_response.json`,
  `coach_approve_response.json`, `coach_history.json`, `coach_settings.json`, `coach_plan_today.json`,
  and for schedule changes `coach_ask_schedule_response.json` / `coach_proposal_schedule_detail.json`
  (a valid move and an invalid one into the past).

| Method | Path | Body / query |
|---|---|---|
| GET | `/coach/proposals` | `status=pending\|applied\|rejected\|all` (default `all`), `limit` 1..200 (default 50) |
| GET | `/coach/proposals/{id}` | |
| POST | `/coach/proposals/{id}/approve` | `{"change_ids": [..] \| null}` |
| POST | `/coach/proposals/{id}/reject` | `{"note": str \| null}` |
| POST | `/coach/ask` | `{"scope": "lift\|run\|week\|general", "ref_id": str \| null, "question": str}` |
| GET | `/coach/history` | |
| GET | `/coach/settings` | |
| PUT | `/coach/settings` | `{"approval_mode": "manual\|auto-low-risk\|auto"}` |
| POST | `/config/rollback/{version}` | existing endpoint, reused for "undo" |

## Proposal

```json
{
  "id": "1",
  "created_at": "2026-10-25T12:49:32.656Z",
  "status": "pending",
  "trigger": {
    "type": "ask",
    "title": "Question about Triceps Extension (Cable) @ Gym 3",
    "detail": "My triceps extension has been stuck for weeks. What should I change?",
    "ref": {"kind": "lift", "id": "127"}
  },
  "question": "My triceps extension has been stuck for weeks. What should I change?",
  "diagnosis": "Triceps extension has not improved its best e1RM for several sessions ...",
  "notes_for_user": "Nothing changes until you approve it. ...",
  "confidence": "medium",
  "changes": [
    {
      "id": "p1",
      "param": "exercise_overrides.triceps_extension.rep_range",
      "label": "Triceps Extension (Cable) rep range",
      "from": [7, 12],
      "to": [8, 12],
      "reason": "A slightly higher floor keeps the load heavier for longer ...",
      "risk": "low",
      "valid": true,
      "validation_error": null,
      "status": "proposed"
    },
    {
      "id": "p3",
      "param": "exercise_overrides.triceps_extension.target_weight",
      "label": "exercise_overrides.triceps_extension.target_weight",
      "from": 30,
      "to": 35,
      "reason": "Jump to 35 kg now.",
      "risk": "low",
      "valid": false,
      "validation_error": "param is not on the allowlist (only engine parameters can be changed, never weights, paces or heart-rate numbers)",
      "status": "invalid"
    }
  ],
  "schedule_changes": [
    {
      "id": "s1", "date": "2026-10-30", "action": "move", "target": "intervals",
      "to_date": "2026-10-31", "new_kind": null, "distance_km": null,
      "reason": "One more day of recovery after the Thursday upper session.",
      "risk": "medium", "valid": true, "validation_error": null, "status": "proposed",
      "label": "Move intervals Fri → Sat"
    }
  ],
  "session_overrides": [
    {"date": "2026-10-26", "session": "Upper", "change": "Keep the first triceps set easy ...", "reason": "..."}
  ],
  "applied_config_version": null,
  "error": null,
  "cost_usd": 0.0971
}
```

`GET /coach/proposals/{id}` returns the same object plus `input_summary` (the compact JSON that was
sent to Claude; for `awaiting_key` and `dry_run` proposals it is what *would* be sent).

- `trigger.type`: `lift_stall | rule_conflict | running_stagnation | data_anomaly | missed_session |
  weekly_review | ask` (`missed_session` is new: a planned session was missed and the engine could not
  make it up, see [Missed sessions](#missed-sessions)). `trigger.ref.kind`: `lift` (id = `variant_id`, as in `/stats/lifts`), `run` (id = run id),
  `week` (id = the Monday, `YYYY-MM-DD`) or `null`/`null`. Several open engine triggers are batched
  into one proposal; its type is the most important one (stall > missed session > conflict > stagnation >
  anomaly),
  `title` says "(+N more)" and `detail` lists them. The engine's `deload` trigger is reported as
  `data_anomaly`. Progression engine v2 (no new values here): the engine's `multi_lift_stall`
  (>= 3 lifts stalled together; a soft deload + retest is already scheduled) is reported as
  `lift_stall`, `fourth_run` (8 consistent weeks: a 4th run may be suggested) as
  `running_stagnation`; the item's `engine_type` keeps the original name.
- `question`, `diagnosis`, `notes_for_user`, `confidence` (`low|medium|high`) are `null` until Claude
  has answered (and stay `null` when it failed). `question` is only set for `ask`.
- `changes[].from` is always the value the engine uses **now** (it replaces whatever Claude claimed);
  `to` is the normalised value. Types: numbers, strings, or a two-element array for rep ranges.
- `changes[].risk` is classified by the **validator**, never by Claude (`low` is deliberately narrow,
  see below). For invalid changes (`valid: false`) it is Claude's own estimate and `label` is the raw
  `param`.
- `changes[].status`: `proposed` (valid, undecided) | `applied` | `rejected` | `invalid`.
- `schedule_changes[]` is always present (`[]` when Claude proposed none); fields below. Statuses:
  `proposed` (valid, undecided) | `applied` | `rejected` | `invalid`. `status` of the proposal counts both
  lists.
- `session_overrides` are advisory free-text notes on planned sessions of the next 7 days. They are
  never applied, and entries that name a weight, a pace or a bad date are dropped by the server. Use
  `schedule_changes` for anything that should really move a session.
- `applied_config_version`: the label (e.g. `v3.3`) of the **latest** config version made from this
  proposal, else `null`. `cost_usd` is the estimated cost of the call (null if nothing was called).
- `error` is a short human-readable reason when `status` is `failed`.

### Proposal status

| status | meaning | actions |
|---|---|---|
| `awaiting_key` | no credential configured yet (no `ANTHROPIC_API_KEY` / `CLAUDE_CODE_OAUTH_TOKEN`), or the subscription login is missing / expired (`error` then says to run `claude setup-token`); the input is stored. Runs by itself once it works (daily job) | reject (dismiss) |
| `queued` / `running` | asked / being answered; poll `GET /coach/proposals/{id}` | none |
| `pending` | answered; at least one valid change waits for a decision | approve, reject |
| `partially_applied` | some changes applied; either the rest are still `proposed` (then it is also *pending*, see filters) or the rest were rejected / invalid | approve (if any `proposed`), reject |
| `applied` | every valid change is applied, **or** Claude proposed nothing (an answer only: `changes` and `schedule_changes` are empty) | none |
| `rejected` | the user rejected it, or none of its changes were valid | none |
| `failed` | API error, refusal or answer cut off (`error` says which); nothing was proposed. "Subscription usage limit reached, retry later" is retried by the daily job for ask / weekly proposals | reject (dismiss) |
| `dry_run` | `ADVISOR_DRY_RUN=true`: input stored, Claude not called | reject (dismiss) |

Filter `status=` on the list: `pending` = `pending` plus `partially_applied` proposals that still have a
`proposed` change (exactly the proposals counted in `plan/today.coach`); `applied` = `applied` +
`partially_applied`; `rejected` = `rejected`; `all` = everything incl. `awaiting_key`, `queued`,
`running`, `failed`, `dry_run`.

## POST /coach/ask

```json
{"scope": "lift", "ref_id": "127", "question": "My triceps extension has been stuck for weeks. What should I change?"}
```

`scope` `lift` needs a `variant_id`, `run` a run id, `week` any date `YYYY-MM-DD` of that week (the
Monday is returned in `trigger.ref.id`), `general` takes `ref_id: null`. Answers **202** with the
Proposal (`status: "queued"`, or `"awaiting_key"` when no key is configured; `"queued"` also in dry-run
mode, which then ends as `dry_run`). The consultation runs in a background task (typically 30-120 s);
poll `GET /coach/proposals/{id}` until the status leaves `queued` / `running`. Errors: **422**
(unknown lift / run, bad date, empty or > 2000 character question, invalid scope), **429** when
`advisor.max_ask_per_day` (default 5) questions were asked today: `{"detail": "Daily limit of 5 coach
questions reached. Try again tomorrow."}`.

## POST /coach/proposals/{id}/approve

`{"change_ids": ["p1", "s1"]}` applies exactly these changes, `{"change_ids": null}` (or no body)
applies all valid `proposed` ones. The selected changes are validated **again** against the config /
plan as they are now (they may have moved); those that no longer pass become `invalid` (`validation_error`
starts with "no longer valid"). The parameter changes are applied as one new config version authored
`advisor` whose reason lists the changes, then the engine re-plans the current and next week. Schedule
changes are written to the planned sessions (author `advisor`, one `decisions_log` row each with the
consultation id) and the engine re-plans **the affected days only**. Returns the updated Proposal
(`applied_config_version` set when a parameter changed, else `null`).

Idempotent: changes that are already `applied` are skipped, approving again returns the same
Proposal and creates no version. Errors: 404 unknown id; **422** a `change_id` that does not belong to
this proposal or is invalid; **409** the proposal is `rejected`, `failed`, `awaiting_key`, `dry_run`,
`queued` or `running`, or a given change was rejected.

## POST /coach/proposals/{id}/reject

`{"note": "not now"}` (body optional). Marks every still-`proposed` change `rejected`, stores the
note and returns the Proposal (`status: "rejected"`, or `partially_applied` if part of it was applied
earlier). Idempotent. `awaiting_key`, `failed` and `dry_run` proposals can be rejected to dismiss
them; 409 while `queued` / `running`.

## GET /coach/history

Every engine config version, newest first (author `engine | user | advisor | seed`). To undo one,
`POST /config/rollback/{version}` (creates a new version with the old content).

```json
{"versions": [{
  "version": "v3.3", "created_at": "2026-10-25T12:49:33.104Z", "author": "advisor",
  "reason": "Advisor (consultation 2): Interval reps (maximum) 6 -> 7: ...",
  "diff": {"running.intervals.reps_max": {"old": 6, "new": 7}},
  "consultation_id": "2",
  "outcome": null,
  "active": true
}]}
```

`consultation_id` is the Proposal id for advisor versions, else `null`. `outcome` appears 21-28 days
after an advisor version: `{"verdict": "improved|neutral|worse", "notes": str, "metric_before":
{"<metric>": number|null}, "metric_after": {...}}` where `<metric>` is e.g. `bench_press_e1rm_kg`,
`program_avg_e1rm_kg`, `aerobic_efficiency` or `interval_pass_rate` (see below).

## GET / PUT /coach/settings

```json
{"provider": "api", "requested_provider": "auto", "credential_configured": true,
 "billing": "api", "model": "claude-opus-5-5",
 "approval_mode": "manual", "api_key_configured": true, "dry_run": false,
 "calls_this_week": 0, "max_calls_per_week": 4}
```

`provider` is the provider that will run (`api` = Messages API paid per token, `claude_code` = a
Claude Pro/Max subscription through headless `claude -p`, `none` = no credential);
`requested_provider` is `ADVISOR_PROVIDER` (`api | claude_code | auto`); `credential_configured`
is `provider != "none"`; `billing` is `api | subscription | null`; `model` the configured model. The
provider keys are additive and never contain a credential. For a subscription run `cost_usd` of the
proposal is `null` (there is no per-token price).

`PUT {"approval_mode": "auto-low-risk"}` creates a config version (author `user`, path
`advisor.approval_mode`) and returns the new settings. `calls_this_week` counts trigger and weekly
calls since Monday, not "Ask coach" questions.

Approval modes: `manual` (default) never applies anything by itself. `auto-low-risk` applies, right
after the answer, only valid changes **the validator classifies as low risk** (rep-range shifts of at
most 1 rep per end, interval rep counts +-1, and the low-risk schedule changes `skip`, `shorten` and
`replace` by an easier kind); the rest stay `proposed`. `auto` applies every valid change, parameter
and schedule. The risk Claude states is never used for this decision.

## GET /plan/today (additive)

```json
{"date": "...", "readiness": {...}, "readiness_today": {...}, "sessions": [...],
 "coach": {"pending_count": 1, "latest_pending_id": "1"}}
```

`pending_count` = proposals with a `proposed` change waiting for a decision (the `status=pending`
list); `latest_pending_id` = the newest one or `null`. Example: `coach_plan_today.json`.

## When Claude is consulted

| Trigger | When | Budget |
|---|---|---|
| open engine triggers (`lift_stall`, `missed_session`, `rule_conflict`, `running_stagnation`, `data_anomaly`, `deload`) | daily 18:30 (not Sundays), all open ones in **one** call; triggers older than 10 days are dismissed, a stalled lift / stagnating metric consulted in the last 14 days is not asked about again | `advisor.max_calls_per_week` (4), minus one call kept for the weekly review |
| weekly review | Sunday 20:00 Europe/Vienna, once per week, open triggers included | counts toward the 4 |
| "Ask coach" | the user | `advisor.max_ask_per_day` (5), not counted in the 4 |

A failed call gives its triggers back (they stay `open` for the next run). Without a key, the
consultation is stored as `awaiting_key` and is run by the daily job as soon as a key exists. With
`ADVISOR_DRY_RUN=true` the input is built and stored (`dry_run`) but never sent.

## Schedule changes

Claude can re-arrange the **current and next week** (never the past, today counts as future):
`schedule_changes[]` of a Proposal.

```json
{"id": "s1", "date": "2026-10-30", "action": "move", "target": "intervals", "to_date": "2026-10-31",
 "new_kind": null, "distance_km": null, "reason": "...", "risk": "medium", "valid": true,
 "validation_error": null, "status": "proposed", "label": "Move intervals Fri → Sat"}
```

| `action` | fields | effect |
|---|---|---|
| `move` | `to_date` (same week) | the session goes to `to_date`; moving onto a rest day swaps the rest day to the old date |
| `swap` | `to_date` (same week) holding exactly one session | the two exchange days |
| `skip` | | the session is closed (`skipped`); nothing is made up |
| `shorten` | `distance_km` (easy / long run only) | the run is capped at that distance (cut at most `schedule.shorten_max_pct` = 50 %) |
| `replace` | `new_kind` = `easy\|rest\|upper\|lower\|intervals\|long` | the session becomes that kind; the engine fills in the content |

`target` is the kind of the planned session on `date` (`upper | lower | easy | intervals | long | rest`,
`easy_run` / `long_run` also accepted). Fields that do not belong to the action must be `null`.
`label` is the human text for the app, e.g. "Move intervals Fri → Sat", "Skip easy run Wed",
"Shorten long run Sun to 8 km", "Replace intervals Fri with easy run".

Validator (each change on its own, in order, on the plan including the earlier valid changes;
`valid: false` + `validation_error`, status `invalid`): only the current and next week, never the past
(neither `date` nor `to_date`); the target must be an open (`proposed`/`approved`) planned session; a
session is changed once per proposal; max 1 hard session per day (`schedule.hard_kinds` = lower,
intervals), never two hard days in a row, the 24 h rule after lower (intervals at least 2 calendar days
after a lower day), max 2 sessions per day, move / swap stay in their week; `shorten` only reduces, by
at most 50 %; `replace` only to an equal or easier kind (`schedule.kind_load`: rest 0, easy 1, upper
2, long 3, lower 4, intervals 4) unless it makes up a missed session of that kind from this week; the
week's runs stay under the weekly volume cap; `reason` may not name a weight or a pace; Claude never
sets weights or paces (the engine recomputes the content). **Risk** (classified by the validator):
`skip`, `shorten` and `replace` by an easier kind are `low`; `move`, `swap` and any other `replace`
are `medium`.

On approval the placement is stored on the planned session (`moved_from`, `change_reason`, a lock the
engine keeps), so later engine runs never undo it; `planned_sessions.changed_by` is `advisor`. The
plan items (see [sync.md](sync.md#plan-json-plantoday-planweek-and-planned_sessions-in-pull)) show
`moved_from` and `change_reason`.

## Missed sessions

Not Claude: the engine's own policy (`app/engine/makeup.py`, thresholds in the config `schedule`
section, config version with defaults added by migration 0008). When a planned session of the current
week passes without a matching activity it is `skipped` and:

- a missed **lower** or **intervals** session takes over the slot of a later easy run (or the rest day)
  of the same week; a missed **upper** only if such a slot exists; a missed **easy** run is dropped; a
  missed **long** run moves to Saturday (`schedule.long_run_makeup_day`) if that is still ahead, else
  it is dropped and the next long run does not progress (it is planned from the last long run that
  happened);
- always under the hard rules above and the weekly volume cap, never in the past, never doubling a day;
- the make-up is stored as a normal planned session: `moved_from` = the missed date, `change_reason` =
  e.g. "Lower missed Tue → moved to Wed, replacing the easy run" (also written to `decisions_log`);
- if no valid placement exists the session is dropped and an advisor trigger `missed_session` is
  created (payload: the missed session, the reason, the slots checked), which a later consultation
  may handle with `schedule_changes`;
- progression is never advanced by a miss: the next interval session repeats the structure derived
  from the last session actually run, lift targets come from the last performance, weekly volume from
  the km really run. A late import of a "missed" session cancels its make-up.

## What Claude may change

Anything not listed is rejected with `validation_error` (`param is not on the allowlist`): weights,
paces, heart-rate numbers, VDOT, the weekly template, the approval mode and the advisor's own limits
can never be changed by Claude (the schedule of single weeks only through `schedule_changes`). Every change also has a maximum step per proposal and is validated
against the earlier valid changes of the same answer.

| `param` | Type and limits | Risk (validator) |
|---|---|---|
| `strength.rep_range` | `[low, high]` ints within 3..20, low < high, each end moves <= 3 | each end <= 1: **low**, <= 2: medium, else high |
| `exercise_overrides.<slug>.rep_range` | same, for a program exercise (catalog slug) | same |
| `exercise_overrides.<slug>.sets` | int 1..6, +-2 | +-1 medium, else high |
| `exercise_overrides.<slug>.variant` | swap to an **active** variant of the same `movement_pattern` or body region, at the current gym or generic, not already in the day | medium |
| `exercise_overrides.<slug>.rest_s` | int seconds 30..600 for a program exercise (rest timer; the user can set it too, `docs/api/exercises.md`) | **low** |
| `lifting.rest_default_s` | int seconds 30..600, the default rest of every exercise (120) | **low** |
| `increments.barbell_kg` / `smith_kg` / `plate_loaded_kg` / `default_stack_step_kg` | 2.5 or 5 | medium (a doubled step: high) |
| `increments.dumbbell_increment_kg` | 2 or 2.5 | medium |
| `increments.bodyweight_added_kg` / `other_kg` | 1.25 or 2.5 | medium |
| `strength.stall_exposures` | int 2..6, +-2 | medium |
| `strength.stall_reset_pct` | 5..15, +-5 | medium (>= 4 points: high) |
| `recovery.deload_red_days` | int 2..7, +-2 | medium |
| `running.weekly_volume_cap_pct` | 0..15, +-5 | medium; raising above 10: high |
| `running.weekly_volume_floor_km` | 10..60 (0 = off), +-20 | medium; raising by > 5 km: high |
| `running.long_run.cap_km` | 8..25 (never above 25), +-3, not below `min_km` | medium; above 21: high |
| `running.long_run.step_km` | 0.5..2, +-1 | medium; above 1: high |
| `running.intervals.reps_start` / `reps_min` / `reps_max` | int 3..10, +-3, `reps_min <= reps_start <= reps_max` | +-1: **low**, +-2 medium, +-3 high |
| `running.intervals.rep_distance_m` | 200..2000 in steps of 50, +-500 | medium; > 300: high |
| `running.intervals.recovery_distance_m` | 100..800 in steps of 50, +-300 | medium; > 200: high |
| `strength.stall_exposures_2x` | int 2..8, +-2 (clean sessions before a stall, lifts trained twice a week; `stall_exposures` is the once-a-week value) | medium |
| `strength.stall_min_days` | int 7..28, +-7 | medium |
| `strength.stall_reset_small_pct` | 2.5..10, +-2.5 (first stall-ladder step) | medium |
| `strength.multi_stall_lifts` | int 2..6, +-2 | medium |
| `strength.big_miss_reps` | int 2..5, +-1 | medium |
| `strength.rung_min_reps` | int 6..10, +-1 | medium |
| `strength.rep_ceiling_cap` | int 12..20, +-3 | medium |
| `strength.deload.sets_pct` / `strength.deload.fatigue_index_pct` | 25..60, +-15 / +-10 | medium |
| `running.interval_pace_tolerance_pct` | 1..6, +-2 | medium |
| `running.volume.cap_abs_km` | 0..4, +-1 | medium; raising above 2: high |
| `running.volume.single_run_factor` | 1.0..1.3, +-0.1 | medium; raising above 1.15: high |
| `running.long_run.step_back_every` / `step_back_pct` / `drift_hold_pct` | int 3..6, +-1 / 10..30, +-10 / 3..10, +-2 | medium |
| `running.vdot.max_step` / `max_per_4w` | 0.25..1.0, +-0.5 / 0.5..2.0, +-0.5 | medium; raising above 0.5 / 1.0: high |
| `running.easy.strides.reps` | int 0..8, +-2 | **low** |
| `running.threshold.reps_max` | int 3..8, +-2 | +-1 **low**, else medium / high |
| `running.threshold.max_work_min` / `running.intervals.max_work_min` | 15..45, +-10 / 10..30, +-6 | medium |
| `running.quality.phase_weeks.<threshold, threshold_vo2max, 5k_specific>` | int 4..16 / 2..12 / 2..10, +-4 | medium |

`running.weekly_volume_floor_km` is a weekly **target** since the progression engine v2 (the cap
grows toward it), not a floor. RIR / RPE are never parameters.

Per-exercise overrides live in the config as `exercise_overrides.<slug>.{rep_range,sets,variant,rest_s}`;
the engine applies them on top of `program.yaml` (which is never edited), so rolling the config back
removes them.

## Outcomes

A daily job (04:15) looks at advisor config versions that are 21-28 days old (`advisor.outcome_after_days`)
and stores an `outcomes` row: the metric for the 28 days before the change vs the days since, a
verdict `improved | neutral | worse` (+-1 % for e1RM / efficiency, +-15 points for the interval pass
rate) and notes. Metric per parameter: `exercise_overrides.<slug>.*` -> best e1RM of that lift
(`<slug>_e1rm_kg`; swaps and rest times are not scored), other strength / increment / deload parameters -> best e1RM
across the program (`program_avg_e1rm_kg`), `running.intervals.*` -> `interval_pass_rate`, other
`running.*` -> `aerobic_efficiency`. Too little data at 21 days waits until day 28, then records
`neutral` ("not enough data"). Outcomes show up in `/coach/history` and in the next consultation's
input (`past_advisor_changes`).

## Cost

Claude Opus 5.5 at $4 / $20 per million input / output tokens, cache reads $0.20, cache writes $5
(prices live in the config under `advisor.price_*`). The frozen system prompt is about 2.3k tokens
(cached), the input about 3.7k tokens (chars / 4) on the real database, limited to 15k
(`advisor.input_budget_tokens`). A weekly review costs roughly **$0.15** (range $0.10-0.30, dominated
by output and thinking tokens at `effort: high`); each call logs its tokens and estimated cost and
stores them on the proposal (`cost_usd`).

## Server configuration

`ADVISOR_PROVIDER` (`api | claude_code | auto`), `ANTHROPIC_API_KEY` or `CLAUDE_CODE_OAUTH_TOKEN`
(optional), `ADVISOR_MODEL` (default `claude-opus-5-5`), `ADVISOR_DRY_RUN`,
`ADVISOR_MAX_TOKENS`, `ADVISOR_MAX_RETRIES`, `ADVISOR_WEEKLY_TIME`, `ADVISOR_TRIGGER_TIME`,
`ADVISOR_OUTCOME_TIME` (see `backend/.env.example`); `SCHEDULER_ENABLED=true` for the scheduled jobs.
The key is never logged (log redaction covers `sk-ant-` patterns) and never returned by any endpoint
(`api_key_configured` is a boolean). CLI: `uv run python -m app.cli advisor {status|run|input}`.
