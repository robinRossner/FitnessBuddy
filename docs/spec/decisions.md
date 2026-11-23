# FitnessBuddy – spec & decisions (step 1)

## Scope
Personal training system. Step 1 (this): backend skeleton, DB schema, Strong CSV importer with an
editable exercise-mapping review, derived strength stats, import report.
Later: Garmin sync, deterministic progression engine, iOS SwiftUI app, Claude advisor.

## Stack
FastAPI · SQLAlchemy 2 (typed `Mapped[]`) · Alembic · SQLite (WAL, FKs on) · pydantic-settings ·
uv / Python 3.12 · pytest · ruff. Deploy: Docker on an Oracle Cloud Always Free ARM VM behind
Caddy (auto-HTTPS); nightly `sqlite3 .backup` (deploy/backup.sh).

## Decisions
- **Ids.** Syncable rows (sessions, sets, runs, planned_sessions) use UUID strings so the iOS app
  can create them offline; sync = upsert by id. Reference tables use integers.
  Strong imports use deterministic UUIDv5 (fixed namespace in `importer.py`): session from the
  start timestamp, set from (start, Strong exercise name, block occurrence, position in block).
  Re-imports therefore never duplicate.
- **Tombstones.** `deleted_at` on syncable tables; nothing is hard-deleted by sync or import.
  A set that disappears from the newest export is tombstoned; a session the user deleted is not
  resurrected by a re-import.
- **Time.** Strong timestamps are naive local start times. Stored as `started_at_local` + `tz`
  (USER_TZ, default Europe/Vienna) + `started_at_utc` (zoneinfo, fold=0 for DST-ambiguous /
  nonexistent times, which the report flags).
- **Exercises → variants.** `exercises` = canonical movement (catalog yaml). Progression is tracked
  per `exercise_variant` = movement + equipment (+ gym for machines/cables/smith/plate-loaded,
  because pulleys and stacks differ). Barbell, dumbbell and bodyweight variants are generic
  (`gym_id` NULL). Variants have an integer id plus a deterministic uuid5 `key`
  (mapping variant name + gym key) that the importer matches on, so renames never duplicate.
  `active=false` (mapping csv column) = the user no longer does it: history kept, hidden from the
  report's review/anomaly sections, never to be planned.
- **Gym periods.** `apps/fitness/data/catalog/gyms.yaml` lists gyms with the `start` date (first session
  there, inclusive; the next gym's start ends the period). `key` is the stable identity (rename
  `name` freely); `same_as: <key|name>` merges a period into another gym (variants, ladders and
  sessions follow). The importer assigns `sessions.gym_id` and the gym-bound variants
  (`"<variant> @ <gym name>"`) from the session date on every import, so editing the file and
  re-importing moves existing sets (set ids do not depend on the variant) and removes variants
  left without sets. Boundaries are chosen from the data (the largest simultaneous top-set jumps on
  machine/cable variants) and written down in the yaml. `overrides:` in gyms.yaml pins single dates
  to a gym.
- **Load convention.** `total | per_hand | assistance`. `assistance` was added for assisted / band
  pull-ups (logged weight = help; lower is harder) so they never mix with weighted pull-ups.
  Dumbbells are `per_hand` (confirmed by the user).
- **Mapping review.** `backend/apps/fitness/data/catalog/strong_mapping.csv` is the user-editable source of
  truth (mirrored into `exercise_name_mappings`). `status`: `auto` (my guess, importable, listed
  for review), `confirmed`, `unmapped` (sets skipped). The `mapping` command never edits existing
  rows; new names are appended as `unmapped` with a suggestion.
- **Overlapping exports.** All `*.csv` under `imports/` are read (non-Strong headers ignored). Per
  workout start time, the file with the latest max date (newest export) wins; its sets replace
  the stored ones.
- **Strong quirks.** Set Order `W/D/F` → set_type warmup/drop/failure; the set ordinal comes from
  row position in its block. The same exercise can occur in several blocks per workout
  (`block_occurrence`). Reps 0 + Seconds > 0 → timed hold (`reps` NULL, `duration_s`). Weight 0 =
  bodyweight (kept as 0). Distance > 0 → cardio row, `fidelity=low`. `;` delimiter, `,` decimals,
  lb/mi units and `Weight Unit`/`Distance Unit`/`Notes` columns are handled.
- **Bodyweight.** `body_measurements` (syncable: client uuid, tombstones; source
  manual|garmin|healthkit|withings) is the log; `bodyweight_at(date)` = latest on/before the date, else the
  earliest after, else engine config `user.bodyweight_kg` (82). REST: `GET/POST/DELETE
  /api/v1/bodyweight` behind a bearer token (`API_TOKEN`; unset = 503), POST is an upsert by id,
  DELETE a tombstone. CLI: `python -m apps.fitness.cli bodyweight add 82` (one manual entry per day,
  idempotent), `stats rebuild`. Bodyweight is a *context signal, not a goal* (user maintains
  weight); `bodyweight.change_flag_pct_4w: 3.0` is reserved for flagging >3 % moves in 4 weeks to
  the advisor (no logic yet).
- **Session kind.** Named sessions (Push/Pull/Legs/Upper/Lower/Callisthenics) keep their name's
  kind; anything else (Morning/Afternoon/… Workout) is inferred from the exercises' movement
  pattern / muscle by working-set share (`apps/fitness/session_kind.py`): ≥ 60 % bodyweight sets →
  calisthenics, ≥ 75 % lower → lower, ≤ 25 % → upper, else `full` (new enum value).
- **Bad durations.** A session longer than `STRONG_MAX_DURATION_H` (default 4 h) gets
  `duration_s = NULL` and is listed as corrected in the report (one session showed 19 h 22 m).
- **Stats.** Epley e1RM for working/failure sets with 1–15 reps (cap configurable) and weight > 0.
  Bodyweight-loaded variants (pull-up, chest-to-bar, dip): (bodyweight on the session date +
  added) × Epley; assisted / band: (bodyweight − assistance), band with assistance 0 skipped;
  no bodyweight at all → added weight only (basis `added_weight`). Sissy squat is excluded from
  e1RM (partial bodyweight). Volume excludes warm-ups. PRs (e1RM, top weight, reps-at-weight) are
  relative to earlier sessions of the same variant; the first session is the baseline.
  Materialised in `exercise_session_stats`, fully rebuilt after each import.
- **Weight ladders.** For machine_stack/cable variants the importer infers `weight_ladder` from the
  distinct weights used. A ladder edited by the user (`ladder_inferred=false`) is never
  overwritten. `next_load` takes the next rung, or `+default_stack_step_kg` above the top rung.
- **Engine config** (`backend/apps/fitness/config/engine.v2.yaml`; v1 kept for history – v2 adds
  `user.bodyweight_kg` and `bodyweight`) is immutable once used and seeded as
  `config_versions` v2 (the active one); every future decision references the config version it used.
  Easy-run ceiling = polarized Zone 1 (below VT1): 80 % HRmax (153 bpm at HRmax 192), or 89 % LTHR
  (Friel Z2 upper limit) once LTHR is known.
- **Swappable sources.** `GarminSource` (activities, laps, daily health) and `PaceModel` are
  Protocols; implementations (python-garminconnect, HealthKit fallback, pace models) plug in
  later without touching consumers.
- **Privacy.** `imports/` (raw exports + reports), `*.db`, `.env` are gitignored; tests use
  synthetic fixtures only. Secrets are `SecretStr` in settings.

## Step 2 – Garmin sync (read-only)
- **Primary source = official Personal API** (`docs/garmin-personal-api.md`): key -> 24 h JWT,
  cached in `backend/var/garmin_token.json` (0600, fingerprinted to key + base URL), renewed with
  < 1 h left, one forced refresh on 401. Key expiry is not exposed by the API (only the e-mail
  reminder / Garmin Connect page), so a future notification can only warn from a user-entered date
  or react to `KeyExpiredError`.
- **Fallback = python-garminconnect** (login based, mocked in tests only; no credentials yet).
  `GARMIN_SOURCE=auto`: Personal API, fallback on `SourceUnavailable` or an empty *recent* window
  (<= 45 days); old history from the login source only on explicit `--source garminconnect`.
  Login at most once per process, failure remembered (Garmin 429s repeated SSO logins).
- **One activities table.** `runs` holds every Garmin activity (`activity_type` = typeKey,
  default views filter `RUN_TYPES`) instead of a rename + FK migration. Ids are uuid5 of the Garmin
  activity id; upserts are keyed on `external_id`; a tombstoned activity is never resurrected.
- **Laps come from the FIT file** (no JSON laps endpoint): original `.FIT` stored under
  `var/fit/<id>.fit`, laps decoded with `fitdecode` (cadence x2, pace = timer / distance).
  `runs.detail_synced_at` marks done, so details are fetched once; failures retry next sync.
- **Daily health** = merge of `daily_summary` (resting HR, stress, body battery, steps), `sleep`
  and `hrv` into one row per (day, source=garmin); only non-null fields overwrite. Training
  readiness stays NULL (not in the Personal API). Weights go to `body_measurements`
  (source `garmin`, deterministic id per day = one per day, latest weigh-in wins, tombstones
  respected) and trigger a stats rebuild.
- **Sync state.** `sync_state(source, kind)` cursor = data complete up to that day. Backfill
  resumes at cursor + 1, commits after every chunk; incremental sync re-reads cursor - 3 days.
  Chunks never exceed the kind's window (sleep 31 d, daily summary 365 d, HRV 90 d, weight 365 d,
  activities 90 d). 429s are waited out a bounded number of times, then the run stops cleanly.
- **Raw + metrics.** `garmin_raw(kind, key)` keeps the JSON for `garmin reparse`;
  `athlete_metrics(day, metric, source)` records Garmin's max HR / LTHR / zone floors / VO2max
  only when the value changes. The engine may later *propose* config updates from it (Garmin: max
  HR 190, LTHR 171 vs config 192 / none); nothing is applied automatically.
- **Secrets.** Key is `SecretStr`, registered with a process-wide log-record redactor (Bearer /
  ApiKey / JWT / registered secrets); errors never contain headers or key text.
- **Band pull-ups.** `Pull Up (Band)` values (0-5) are band levels, not kg: no e1RM at all and no
  e1RM / top-weight PRs; assisted-machine variants keep bodyweight - assistance (rep PRs at an
  unchanged band level are still tracked).
- **Scheduler.** APScheduler `BackgroundScheduler` in the FastAPI lifespan, only with
  `SCHEDULER_ENABLED=true`: 03:30 all kinds, `SYNC_DAY_TIMES` (07:30, 08:30, 09:30, 11:30, 15:30,
  19:30) activities + daily summary + sleep + HRV (HRV was added 2026-11-01, see "Morning HRV and
  manual sync"). One sync at a time (lock). `on_new_data()` is the empty hook
  where the engine run will go.

## Step 3 - training engine
- **Gym fixes.** `gyms.yaml` got `overrides: [{date, gym}]` for single days that belong to another
  gym, and a gym can be split in two periods when its machines were replaced (same place, but
  machine/cable variants are not comparable across the split).
- **Config in the DB.** `config_versions` got parent_id / author / reason / diff / meta. `create_version`
  (dotted-path changes), `rollback` (new version with the old content; Garmin values seen so far count as
  handled so the rollback sticks until Garmin reports something newer), `apply_garmin_hr` (hook after a
  sync; only if `running.heart_rate.source == garmin`). Every decision stores `config_version_id`.
- **HR zones** are derived from LTHR (Friel run zones, inclusive upper bound = round-half-up(pct x LTHR)):
  at LTHR 171 Z1<=145, Z2 146-152, Z3 153-161, Z4 162-169, Z5a 170-174, Z5b 175-181, Z5c 182+. Easy
  ceiling = top of Z2 (152), low end 65 % HRmax (124), hard reps target Z4..Z5a (162-174).
- **Lifting**: double progression in [7,12] (per-exercise override), per-set targets from the last
  exposure; all sets at the top at the same load -> `next_load`; first set below the range -> one step
  down; else +1 rep per set. Stall = no best-e1RM improvement over the last 3 exposures vs. everything
  before -> -10 % rounded DOWN on the load grid / ladder (+ `lift_stall` trigger; sets identical every time
  are flagged `identical_exposures`); the reset is remembered via decisions_log (`reset_day`) and only
  exposures since the reset count. Deload = 4 consecutive red days -> 7-day window with sets -40 %
  (min 1) and load -10 %. Timed holds: best hold + 5 s.
- **Readiness**: signals HRV (vs previous-7-day mean, plus Garmin status), resting HR, sleep (worse of
  duration and score). Any red -> red, except a red SLEEP alone (needs another flagged signal, else
  yellow); 2 yellow -> yellow; 1 yellow stays green with a note; no data -> green/"no data". HRV <= -15 %
  vs baseline on 3 consecutive days -> `data_anomaly` trigger.
- **Running**: `VdotPaceModel` (Daniels formulas, check: VDOT 40 = 5 km 24:06 / 10 km 50:01), seed 10 km
  in 52:30 (VDOT 37.79); efforts (best contiguous >= 5 km, avg HR >= 88 % LTHR, last 12 weeks) can only
  raise it. Selectable with `running.pace_model`. Easy run PASS = avg HR <= 152 and >= 80 % of time <= 152
  (Garmin zone seconds, linear split of the straddling zone; else laps); decoupling > 5 % flagged. Interval
  laps: FIT intensity if it separates work from recovery (auto laps all say "interval"), else a pace
  heuristic. Pass = mean work HR <= zone top and mean (work lap HR - recovery lap HR) >= 12 bpm; then +1
  rep (4..6 reps), at max reps a faster pace (+0.5 VDOT on the interval scale) and back to 4 reps; no
  state is stored, progression is replayed from the last interval session. Long run +1 km per clean long
  run (cap 21). Weekly km cap = max(`weekly_volume_floor_km`, 1.1 x max(last week, 4-week mean)); the floor is 18 km
  (config v3.1, created with `engine config set`, v3 itself is untouched). The interval session gets the
  cap minus the minimum easy (3 km) and long run (8 km): warm-up/cool-down shrink proportionally (min 1.0 /
  0.5 km); if the planned rep count still does not fit and the progression only added a rep, the last
  session's rep count is kept (`intervals:volume_hold`, reason logged). Then long run, then easy run shrink;
  if everything at its minimum still exceeds the cap a `rule_conflict` trigger is raised.
- **Scheduling**: template from config; an interval day within 2 calendar days (24 h rule) after a heavy
  lower day is swapped with the earliest later easy/rest day (downgrade to easy + trigger if none).
  Slot ids (`thu_upper`) stay stable when days move.
- **Plans**: stable planned-session ids (uuid5 of week + slot); readiness adjustments only for today;
  `rule_conflict` trigger when readiness is red but progression says increase. `advisor_triggers`
  (type, payload, dedupe_key, status) is the hand-over to the future advisor. Decision rows are only
  inserted when they differ from the latest row of the same rule on the same subject.
- **Thin running history is real.** The Garmin fallback backfill finished: Garmin has no activities before
  the account's start, so VDOT, efficiency trends and weekly volume are based on ~2 months. Stagnation
  triggers and the volume cap will become meaningful only as history accumulates.

## Step 6 - Claude advisor and coach API

Contract and examples: [../api/coach.md](../api/coach.md). Code: `backend/apps/fitness/advisor/`.

- **Claude proposes parameters only.** The engine keeps computing weights, reps and paces. Claude's
  answer is strict JSON (structured outputs, Pydantic schema); each `proposed_change` is checked on its
  own against an allowlist with types, hard limits and a maximum step (`advisor/validator.py`). Anything
  else (weights, paces, HR numbers, VDOT, the weekly template, the approval mode, the advisor's own
  limits) is stored as `invalid` with the reason and never applied. **The validator classifies the risk**;
  `low` is only a rep-range shift of at most 1 per end and interval reps +-1. Session overrides are
  advisory text; ones that name a weight or a pace are dropped.
- **Per-exercise overrides** (`exercise_overrides.<slug>.{rep_range,sets,variant}`) are config, not
  `program.yaml`: the engine overlays them on the program when loading its inputs, `program.yaml` stays
  the user's file and a config rollback removes the overrides. An exercise swap must target an active
  variant with the same movement pattern or body region at the current gym (or generic).
- **Live program file.** The engine reads `program.yaml` from the data dir next to the database
  (`settings.live_program_path`: `backend/var/` locally, `/app/var` = `deploy/data/` on the server;
  `deploy/push.sh` rsyncs `backend/apps/fitness/data/` with `--delete`, so a file there would be overwritten). It is
  seeded once by copying the repo's `backend/apps/fitness/data/program/program.yaml`; editing the repo file later
  has no effect, delete the live file to re-seed. The app's "Update template"
  (`PUT /api/v1/program/{kind}`) and `engine program generate` write the live file (only that kind's
  list, comments kept, timestamped backup in `program.backups/`, last 20). Not a config override, so a
  config rollback does not undo it; restore from a backup instead.
- **Approval modes** (`advisor.approval_mode` in the config, so a change of mode is itself versioned):
  `manual` (default), `auto-low-risk`, `auto`. Applying = one `create_version(author="advisor")` per
  approval, the reason lists the changes, then the engine re-plans (`rerun_engine`). Rollback is the
  existing endpoint.
- **When Claude is called**: open `advisor_triggers` (batched, daily 18:30 except Sundays), a weekly
  review (Sunday 20:00 Europe/Vienna, once per week) and the user's "Ask coach". Budget guard in the
  config: `advisor.max_calls_per_week: 4` (a call is kept back for the weekly review),
  `advisor.max_ask_per_day: 5`, asks not counted in the 4. `ADVISOR_DRY_RUN=true` stores the input only.
  No `ANTHROPIC_API_KEY` -> status `awaiting_key`, the daily job runs them once a key exists.
- **The API call**: `claude-opus-5-5` (`ADVISOR_MODEL`), adaptive thinking, `effort: high`, non-streaming,
  `max_tokens` 16000, frozen system prompt with the cache breakpoint (all volatile data in the user
  message), server-side refusal fallback (`fallbacks: "default"`, beta `server-side-fallback-2026-07-01`,
  hence `client.beta.messages.parse`), `stop_reason` checked before content, SDK retries with backoff.
  Tokens and an estimated cost are logged and stored per consultation.
- **Outcomes**: 21-28 days after an advisor version, `outcomes` gets a verdict (improved / neutral /
  worse) from the e1RM of the affected lift(s), aerobic efficiency or the interval pass rate; it is shown
  in `/coach/history` and fed back into later consultations.
- **Tables** (migration 0007): `advisor_consultations` (+ status, kind, trigger_info, question, usage,
  cost, error, dry_run, week_start, applied_config_version_id; `request` = input, `response` = output),
  new `advisor_changes`, `outcomes` (+ kind, verdict, consultation_id, config_version_id). The link
  version -> consultation lives in `config_versions.meta["consultation_id"]` (adding a column to
  `config_versions` would recreate a table that other tables reference).

## Step 7 - missed sessions and schedule changes
- **Missed-session policy is deterministic** (`apps/fitness/engine/makeup.py`), run by `run_engine` right after
  `reconcile` for the sessions it just marked `skipped`. Priority (config `schedule.makeup_priority`:
  lower, intervals, upper, long, easy): a missed lower / intervals session takes over the slot (same
  planned-session id, kind changes) of a later easy run or the rest day of the same week; upper only if
  such a slot exists; easy is dropped; long moves to Saturday if still ahead, else dropped and the next
  long run does not progress. A make-up never doubles a day. Hard rules (config): max 1 hard session
  (lower, intervals) per day, never two hard days in a row, the 24 h rule after lower
  (`recovery.hard_after_heavy_lower_h`), max 2 sessions per day, never the past, the weekly volume cap
  (the week's remaining runs at their minimum km must fit under cap minus km already run). No valid
  slot -> drop + advisor trigger `missed_session` (not for a dropped easy run, and not for misses older
  than 7 days). Every make-up / drop is a `decisions_log` row (`makeup:moved|dropped|cancelled`) with a
  human reason; the destination keeps `moved_from` + `change_reason`.
- **No progression code for misses**: interval progression is replayed from the last interval session
  that was run, lift targets from the last performance, volume from the km run, the long run from the
  last long run that happened. A skipped session therefore advances nothing. A late-imported activity
  heals the skipped session (existing behaviour) and now also cancels the make-up placed for it.
- **Placements are locks.** `planned_sessions` got `moved_from`, `change_reason`, `changed_by`
  (`engine|advisor`) and `schedule_lock` (JSON: day, kind, distance cap, source, action, `makeup_for`,
  `consultation_id`). The planner reads the locks of a week (`data.slot_placements`), puts the slots
  there, keeps them out of the recovery swap and never moves them, so every engine re-run keeps a
  make-up or an approved coach change; only the content (weights, paces, distances) is recomputed.
  Skipped / deleted slots are excluded from the lower-day maths. The engine's own recovery swap also
  records `moved_from` / `change_reason` now.
- **Plan items are additive**: `adaptive`, `adapts_on`, `moved_from`, `change_reason`, `status` on
  `/plan/*` and the sync pull (one renderer, `persist.planned_to_dict` / `adaptive_fields`).
- **Coach leverage is structured and validated.** `schedule_changes` (move, skip, swap, shorten,
  replace) next to the parameter changes; the same pure rules as the engine (`schedule_rules.py`); only
  the current and next week; shorten <= 50 %; replace only to an equal or easier kind unless it makes
  up a missed session; stored in `advisor_schedule_changes` (migration 0008); ids `p<n>` / `s<n>`,
  unique across both lists; risk: skip / shorten / replace-easier low, move / swap medium. Approval
  modes apply (`auto-low-risk` applies the low ones). Applying writes the lock, a `decisions_log` row
  with the consultation id, and re-runs the engine for the affected days only. `session_overrides`
  stay advisory text.
- **Config**: a new `schedule` section (`ScheduleConfig` defaults; `apps/fitness/config/engine.v4.yaml` = v3 + the
  section, for fresh databases once `engine_config_path` points to it). Migration 0008 adds the section
  to a running database as a derived version (`<family>.<n>`, author `engine`) that keeps the active
  content, so no derived version is lost.
- **Provider errors** (`AdvisorCallError.kind`): `auth` -> `awaiting_key`, `usage_limit` -> `failed` with
  a fixed message and a retry by the daily job (ask / weekly) or the next trigger batch; the rest are
  normal failures. A subscription run stores `cost_usd = null`. `GET /coach/settings` carries
  `provider`, `requested_provider`, `credential_configured`, `billing`, `model`.

## Progression engine v2 (2026-10-25)

Sources: [lifting research](../research/lifting-progression.md) and
[running research](../research/running-progression.md) (both 2026-10-25), with the athlete's fixed
decisions taking precedence. Code: `apps/fitness/engine/lifting.py`, `quality.py`, `vdot.py`, `running.py`,
`planner.py`, `data.py`, `makeup.py`; config `apps/fitness/config/engine.v6.yaml` (a running database gets the
keys as a derived version through the data-only migration 0010).

**Fixed decisions.** Every working set stays at 0-1 RIR; the engine never prescribes or asks for
RIR / RPE (it is not even on the coach's allowlist). Rep range 7-12 by default, per exercise via
`exercise_overrides.<slug>.rep_range`. Upper / Lower / Upper plus three runs (Wed easy, Fri quality,
Sun long). Lifting and running have equal priority. Every threshold is a config key; the ones the
coach may tune are on the validator allowlist with bounds and risk levels.

**Lifting.**
- Signal: reps at a load (`S` over the working sets at the main load; a yellow day with a set
  fewer compares the first k sets). Progress event = load PR (all sets >= the range bottom), rep PR
  at the same load, or best-set e1RM +1 %.
- Noise vs drop: 1-2 reps fewer = noise, same target again (from the better of the last two). A drop
  of >= 3 reps, or two clean drops in a row, steps one rung down (rebuild).
- Gate (two-strike): load goes up when every set reaches the (effective) top once, or every set is
  >= top-1 on two consecutive clean sessions. Otherwise +1 rep per set, capped at the top.
- Rung rule: Epley predicts the reps at the next available load (barbell / smith / plate +5, dumbbell
  2 or 2.5, the stack ladder's next rung, else +default step). If fewer than 8 (config
  `rung_min_reps`), the rep ceiling rises (`effective_rep_range`, max `rep_ceiling_cap` 15) until the
  jump predicts >= 8. Above 15 the rung is infeasible: state `stalled`, advisor trigger (smaller
  increment or variation). After a jump the per-set targets are the Epley prediction, not a blind 7.
  A first session at a new load with set 1 <= 5 reps steps back at once; below 7 it gets one more try.
- Stall: no progress event in N CLEAN sessions and >= 12 days; N = 4 for lifts on two template days
  (upper), 3 for one (lower) (`stall_exposures_2x` / `stall_exposures`). Replaces the v1
  "3 exposures without an e1RM gain" rule.
- Censoring (misses don't count, hits always do; the reason is in the decision inputs
  `censored`): yellow / red readiness on the day, lower-body lifts on the day of or after intervals
  or a non-interval run >= 10 km (day-level 24 h rule), the first 2 sessions of a variant (swap),
  after a break >= 10 days or after a gym change, and deload-week sessions.
- Stall ladder per lift (decisions_log `output.ladder`, read back by `data.load_ladder`): step 1 =
  -5 % rounded down to an available load, rebuild; step 2 = advisor trigger `lift_stall` with
  `proposal: rep_range_or_variation` (the engine never picks variations), load held; step 3 = -10 %
  last resort; beyond that the coach decides (`ladder_exhausted`). An episode ends when the lift beats
  its pre-ladder best e1RM. A decided step is re-presented identically until trained, so re-runs do
  not change the planned session.
- Multi-lift stall: >= 3 lifts stalled (or reset) within 14 days -> no per-lift resets; one soft
  deload week from the first open lifting day (sets halved and rounded up, min 1, loads kept), then
  a retest of the old loads (beat the old reps). Advisor trigger `multi_lift_stall` (mapped onto the
  existing `lift_stall` proposal type, so the coach API has no new values). A failed retest goes
  straight to ladder step 1. No second multi-lift / fatigue deload within 28 days.
- Deloads are reactive only: >= 4 consecutive red days (existing rule, new form: sets halved, loads
  kept) or the fatigue index (share of clean sessions that were a miss or a regression) >= 40 % in
  two complete weeks in a row. A lifting deload pulls the long-run step-back forward when it is
  due the next week (`align_running_step_back`).
- Return after a break (days since the lift's last session): <= 10 nothing; 11-21 same load, one set
  fewer in the first session; 22-42 -5 % and one set fewer for the first week; > 42 -10 % and half
  the sets for the first week. The first 2 sessions after a break are censored (re-baseline).
- Plan output per exercise: `effective_rep_range`, `progression_state` (`progressing | grinding |
  stalled | deload | retest | rebuild`), `progression_note`.

**The −10 % resets of 2026-10-25.** The v1 engine had flagged several upper lifts at once and planned −10 %
resets for the same week (and the coach suspected copied or prefilled logs). The v2 engine converts
every untrained v1 `stall_reset` decision at read time (`data.legacy_windows`): with >= 3 of them on
the same day a soft deload week (reason `legacy_reset`), then a retest of the old
loads; a single one becomes a retest only. The conversion is logged once
(`deload:legacy_reset_converted`); migration 0010 dismisses the v1 `lift_stall` triggers that
announced the resets, the engine raises one `multi_lift_stall` trigger instead.

**Running.**
- Interval pass by pace: >= 80 % of the work reps within 3 % of the target (faster is fine) and the
  last rep at most 3 % slower than the first, all prescribed reps done. HR never fails a session: the
  last rep's peak HR (lap max HR: the closest a lap file gets to "the final 60 s") above the type's
  ceiling (threshold: Z4 top, 99 % LTHR; VO2max / 5k: 108 % LTHR = ~97 % HRmax) = `pass_hard` (hold),
  below the green line (95 % / 102 % LTHR) = `green` (passed easily, VDOT evidence). Recovery-lap HR
  drops are a multi-week trend only. Run analysis (`/runs/{id}/analysis`) uses the same rules:
  `pace_ok` per lap, `hr_ok` = lap peak <= the ceiling, `target_hr_low/high` = green line / ceiling.
- One lever per week, in order: reps -> rep length -> shorter recovery (threshold only) -> pace. The
  pace lever is a VDOT step and needs 2 green sessions in 3 weeks; otherwise the session holds at its
  maximum. Fail -> repeat; 2 fails of the same session in 3 weeks -> one rep back; `pass_hard` holds.
  Hard minutes are capped (threshold 30, VO2max / 5k 20).
- Friday cycle: 12 weeks threshold, 8 weeks threshold / VO2max alternating, 6 weeks 5k-specific
  (alternating with threshold), a 5 km time trial, then the cycle repeats. A phase week counts only
  when its quality session was run, so a missed Friday repeats the same session. The cycle start is
  the first recorded phase decision (or `running.quality.cycle_start`). Wednesday easy runs carry
  6 x 20 s strides (`structure.strides`).
- VDOT is persistent state (decisions_log `output.vdot_record`): the pace-model fit only anchors the
  first record. Up at most +0.5 per update, +1.0 per 28 days, one update per week, on a time trial /
  hard effort (best >= 5 km stretch at >= 88 % LTHR), or 2 green sessions with an aerobic-efficiency
  trend >= +1 % (4 vs 4 weeks), or the pace lever. Never down after one bad session: only by the
  layoff table (15 / 29 / 57 days without a run: -2 / -5 / -8 %, once per break) or 3 genuine fails
  in 4 weeks (-0.5). First week back after >= 15 days off: easy + strides instead of the quality
  session.
- Volume: weekly cap = max(4-week actual mean x 1.10, mean + 2 km) (weeks without runs count as 0).
  The 18 km `weekly_volume_floor_km` is now a target the cap grows toward. A single run is at most
  1.1 x the longest run of the last 30 days. Fitting order under the cap: warm-up / cool-down of the
  quality session, its added rep (volume hold), then the easy run, then the long run (both down to
  3 km); a long run pushed below 8 km raises a `rule_conflict` trigger so the coach can drop the easy
  run instead.
- Long run: longest recent long run + 1 km; every 4th week a step-back (-25 %); drift gate on the
  last long run (>= 60 min): decoupling > 5 % holds, > 8 % cuts 10 %; cap 20 km.
- Missed runs: never made up in volume. A missed run moves by at most one day onto a later easy run
  or the rest day if the hard rules allow; otherwise it is dropped (no advisor trigger) and the same
  session is planned again next week. Lifting make-ups keep the step-7 rules.
- The easy run the day after a lower-body session: an HR fail becomes a note (`exempt`).
- 4th run: suggested to the coach (`fourth_run` trigger, mapped onto `running_stagnation`) after 8
  weeks with >= 3 runs each, a 4-week mean >= 28 km and a long run >= 14 km in the last 4 weeks;
  never added automatically.
- Missing data: without HR the evaluation is pace-only (`hr_safety_exceeded: null`, no greens, so
  VDOT only rises through efforts); without laps a session cannot be judged and is repeated;
  without LTHR the HR lines are omitted. Wrist HR lags on short reps (no chest strap): that is why
  HR is only a ceiling.

**Advisor.** The input has a `progression` section (per-lift state, effective range and note, the
stall-ladder position, censored misses of 14 days, the deload window, the running phase, VDOT
record and volume-cap reasoning). The frozen system prompt states the v2 rules and the coach's role:
multi-lift stalls (find the shared cause), variation choice and rep ranges, pain / illness (rest and
a professional), running vs lifting trade-offs and the 4th run.

**Deviations from the research reports (and why).**
- VDOT from a race / time trial is capped like any other update (+0.5, +1.0 per 4 weeks), not the
  report's "up to +2 with a race": the athlete's decision on VDOT steps takes precedence.
- "2 easy passes **and** an efficiency trend" (the athlete's wording) instead of the report's "or";
  the pace lever alone may step with 2 green sessions.
- Single-run cap 1.1 x without the report's "+1 km allowance" (`single_run_abs_km` = 0, tunable).
- Yellow readiness keeps the existing rules (-1 set; intervals -> easy, now with strides); red keeps
  "lifting -> rest" instead of the report's "half sets, -5-10 %".
- Not built (left to the coach or later): planned soft deloads every 6-8 weeks, adding / removing
  sets per muscle, the in-session "set 1 below the floor -> change the load now" hint, heat
  allowances on easy runs, the steady finish on long runs, hill sprints.
- The 24 h censoring is day-level (runs have no time-of-day in the engine's data): a lower session
  on the day of or after intervals / a run >= 10 km is censored.

## Deload cancel and volume hold (2026-10-25)

**Cancelling a deload.** The athlete can cancel the soft deload of a week
(`python -m apps.fitness.cli engine deload cancel --week YYYY-MM-DD [--reason ...]`, or
`POST /api/v1/engine/deload/cancel`, [docs/api/engine.md](../api/engine.md)). It is a `decisions_log`
row of kind `deload_cancel` (author user, subject = the week's Monday) that `data.load_windows`
applies to every persisted multi-lift-stall / fatigue-index / converted-v1 window overlapping that
week (`data.cancel_window`): the deload part from that week on disappears (a part trained in an
earlier week stays), the retest of the old loads starts at once with normal set counts (progression
note "Retest at old load ... beat ..."), the other lifts follow the normal rules, and the running
step-back is no longer aligned with it. In a cancelled week the planner creates no new deload
(fatigue index) and turns a multi-lift stall straight into the retest. Idempotent (a week cancelled
twice, or without a deload, changes nothing); the reactive red-streak deload is not cancellable.

**Volume cap: hold last week.** `cap = max(mean4 x 1.10, mean4 + 2, hold)` with
`hold = km of the last completed week` when `running.volume.hold_last_week` is true (default).
Holding a volume you just completed is low risk, so the cap may follow it, but only up to
`running.volume.hold_limit_factor` (1.1) x the highest weekly km of the 4 weeks before that week, and
not at all when an easy or long run of that week had an HR fail (the day-after-lower exemption still
applies) or an HR-drift flag (`running.hold_volume`; interval sessions are not judged). The
single-run cap (1.1 x the longest run of 30 days) is unchanged. The plan's volume reasoning names the
hold (or why there is none). Keys arrive as a derived config version through the data-only migration
0011; both are on the advisor allowlist (`hold_last_week` low risk; `hold_limit_factor` medium, raising
above 1.1 high).

## Watch push, exercise names, readiness Why (2026-10-28)
**Garmin workout push (planned runs -> watch).** The Personal API is pull-only, so the push uses
python-garminconnect through the shared `GarminConnectLogin` (the only login path; the push's
process-wide login waits 6 h after a failure). Stateless: our workouts carry
`[FitnessBuddy ps=<id> d=<date> h=<hash>]` in the description and anything without the marker is never
touched. Window: today + `GARMIN_PUSH_DAYS` (2; 1 since 2026-11-06, see below) of open runs with no activity that day. A changed
workout is replaced (new one uploaded first, old one deleted after); past, done or removed ones are
unscheduled and deleted. Names are dated ASCII, e.g. "Thu 29.10 Easy 5 km". Easy/long = one distance
step with an HR range; intervals = warm-up, repeat x (pace-range rep + HR secondary, jog), cool-down.
Pushes run at 19:45 and 05:00 Europe/Vienna, after engine re-runs (server only, only when the wanted
set changed) and on demand (`POST /api/v1/garmin/workouts/push`, `garmin push-workouts [--dry-run]`).
Contract: docs/api/garmin.md.

**Exercise names.** Names may repeat across equipment: creating a same-named exercise adds a variant
to the existing exercise; only same name + equipment (+ gym for machines) is a duplicate (409).
Renaming an exercise renames all its live variants to `<name> (<type>) @ gym`, writes
`exercise_overrides.<slug>.variant` for program lines (the plan keeps finding the machine) and sets
`edited_by_user`, so neither the catalog sync nor a Strong re-import puts the old name back.

**Exercise order in a workout** is the sets' `exercise_order`, renumbered 1...n on reorder (no new API
field). "Skip exercise" became "Delete exercise" with a confirmation; tombstones as for a set delete.

**Readiness Why list:** one line per scoring factor (HRV, resting HR, sleep) with value vs baseline and
its effect, decisive first, with explicit "no data" lines (before, yellow/red days listed only the
flagged factors).

## Easy pace calibration (2026-10-28)
Daniels' easy range (59-74 % of VDOT) came from the quality VDOT, which only moves through races,
time trials and quality sessions, so the prescribed easy pace (5:55-7:05/km at VDOT 38) was slower
than the athlete's easy runs at an easy HR (~5:50/km). Now the easy and long run range is
calibrated from his own easy runs (`apps/fitness/engine/easy_pace.py`, config `running.easy_calibration`):
a run counts when it is a steady run (no intervals, not a quality-session day) of >= 25 min in the
last 28 days that passed the easy-run HR rule on its own merit (no day-after-lower exemption), had an
average HR <= the easy ceiling - 2 bpm (154), HR drift <= 5 % (laps needed) and <= 25 m/km climbing.
With >= 3 of them, their median pace becomes the fast end (74 %) of the easy range: the "easy VDOT"
is that pace's VDOT, clamped to [quality VDOT, quality VDOT + 4]. It never makes the range slower
than Daniels, and HR still leads on the day. It is recomputed from the window on every run (no
state), logged once per week as `running:easy_pace_calibration` (subject = week) and shown in the
plan (`pace.basis`, `pace.reason`, docs/api/sync.md). Threshold, interval, 5k and time-trial paces,
the recovery-jog distance and the quality VDOT itself are unchanged. Keys arrive as a derived config
version through the data-only migration 0013 (no new YAML, like 0011); on the advisor allowlist:
`enabled` low risk, `max_vdot_gain` 0..6 (above 4 high), `window_days`, `min_runs` (below 3 high),
`hr_margin_bpm` medium. Until enough genuine easy runs (>= 80 % of their time at or below the HR ceiling)
exist, the range stays Daniels.

## VDOT from interval reps (2026-10-28)
The quality VDOT stayed put although the athlete ran clearly faster interval reps (rep HR in Z3-Z4):
the only path that looked at those sessions was the pace model's fit (best >= 5 km stretch at >= 88 % LTHR),
which averages reps and jogs, and green sessions only count for planned quality sessions (+0.5 at a
time). So the threshold pace was far slower than the athlete's rep pace.

Now the work reps are a measurement (`apps/fitness/engine/interval_vdot.py`, config
`running.vdot.from_intervals`): a session of the last 70 days counts with >= 4 work laps of
2-8 min and >= 300 m (warm-up, jogs and GPS junk laps drop out), slowest - fastest <= 8 % of the
median and the last rep <= 5 % slower than the first, and a median rep peak HR >= 90 % LTHR (a fast
"pace" at an easy HR is GPS, not fitness; without HR it does not count). Its median rep pace is
taken as the I pace (98 % of VDOT, conservative: reps with an average HR below Z5 were sub-maximal,
so the true I pace is at least that fast; reps at Z5 count as I pace exactly). Evidence loses
0.25 VDOT per week after 28 days; the best session wins. When it beats the quality VDOT,
`vdot.update_vdot` raises it straight to that value in one update, at most +3 (source
`interval_reps`, a `running:vdot` decision with the sessions in `inputs.interval_reps`; the advisor
summary shows the record and its reason). It is a measurement, so it is not held to the inference
path's +0.5 / +1.0 per 28 days, but it uses up that room (no inferred rise for 4 weeks after a big
jump). The one-update-per-week rule still applies (a capped remainder follows a week later while the
age-discounted estimate is still higher). Drops are unchanged (layoff table, 3 fails); reps from
before the last drop never count, and the estimate only decays with age, so it cannot oscillate and
re-runs are idempotent. Easy calibration keeps easy VDOT in [quality, quality + 4], so the easy range
follows the quality VDOT up when it passes the easy runs' own estimate.

Example: three interval sessions measured 45.8, 45.6 and 45.8 after the age discount, so the quality VDOT
moved up by the +3 cap in one update and every pace of the plan followed. Keys arrive as a derived
config version through the data-only migration 0014; on the advisor allowlist (`enabled` off low /
on medium, `max_step` 0.5..4 above 3 high, `window_days` above 70 high, `min_reps` below 4 high,
`age_free_days` above 28 high, `age_discount_per_week` below 0.25 high). `POST /api/v1/engine/run`
(docs/api/engine.md) re-plans immediately.

Progress tab (same day): the VDOT chart showed the pace-model fit while the plan used the
state VDOT. `/stats/running` now returns the state VDOT as the headline: `kpis.vdot_current`
and the `vdot` step series are the `running:vdot` records of the decision log (value, source,
delta, reason), so the chart's last value is exactly what the plan's paces come from. The per-run
fit stays as the secondary `vdot_fit` series / `kpis.vdot_fit_current`, drawn as a thin dashed
"race-fit estimate" line (it is the only trace of the fit between records). Additive: older iOS
builds read `vdot` / `vdot_current` and so show the plan's VDOT too.

## Receipts moved out (2026-10-31)

The Receipts server (receipts API, extraction queue, photo archive) left this backend and now lives in
its own repository (`~/Projects/Receipts App`, own deployment in `/opt/receipts`). Same VM and
domain, no shared code or data; this stack's Caddy routes `/api/v1/receipts/*` to it. The decisions
that were recorded here moved to that repository's `docs/decisions.md`.

## Morning HRV and manual sync (2026-11-01)

- **HRV was missing in the morning.** Daytime runs fetched activities, daily summary and sleep only;
  HRV came with the 03:30 run, before last night's HRV exists, so a day's HRV stayed empty until the
  next 03:30. `LIGHT_KINDS` now includes HRV and the day times are 07:30, 08:30, 09:30, 11:30, 15:30,
  19:30 (Garmin's HRV can land after the sleep data and after waking). One more request per light
  run.
- **"New data" means changed data.** `new_data` used to be true for any returned row, and the
  3-day overlap returns rows every time, so every sync re-ran the engine. `KindResult.changed` now
  counts rows that are new or whose stored values differ (`rows` keeps its meaning: rows read and
  written); `SyncReport.new_data` is true only when something changed or a detail (laps / FIT) was
  fetched, so a late HRV still triggers the engine and readiness. `activities_inserted` counts true
  inserts. The engine's workout push already skipped unchanged plans (fingerprint), so this mainly
  saves the engine run.
- **Manual sync.** `POST /garmin/sync` (docs/api/garmin.md) runs the light kinds synchronously with an
  in-memory guard: 30 s cooldown, 30 per local day, and after a Garmin 429 a pause until its
  Retry-After (15 min if unknown). The login-based fallback may only resume its cached tokens there
  (no fresh SSO login, which is what gets accounts blocked for days). Garmin publishes no
  Personal API limit; one manual sync costs 4 requests plus one per new activity.

## Easy-run time share from Garmin zones (2026-11-02)
- **Rule.** The 80 % part of `evaluate_easy_run` is Garmin's own seconds in Z1-Z3 over all zone
  seconds, with no bpm conversion, so it follows the zones set on Garmin (the user moved to %HRR
  with their real resting HR). Splitting a zone at the bpm ceiling was only an estimate once Z4 no
  longer started at the ceiling. Laps against the ceiling remain the fallback without zone seconds.
- **Aligned with Garmin's zones (the user's call).** With `running.heart_rate.source: garmin` and
  valid floors, the easy target is Garmin's (`engine/hr.py` `garmin_zones`): Z2 floor .. Z4 floor - 1,
  121-150 at the %HRR floors 86/121/130/151/172, so the watch target, the avg-HR rule and Z1-Z3 end at
  the same bpm. LTHR zones stay as the fallback. Quality sessions stay on LTHR, see below.
- **Floors are checked first.** Five strictly ascending values, Z1 floor >= 50, Z5 floor > 100 and
  <= max HR. We have only seen `HR_MAX` payloads; if `HR_RESERVE` reports percentages they fail this,
  a warning is logged once and the LTHR zones are used.
- **A past run is judged by the zones it was run in** (`ZoneBook.for_run`): the easy verdict, the
  volume hold, easy-pace samples, the VDOT effort label, run analysis and stats. Planned targets use
  the latest floors. So a zone change alone does not flag old runs, move the hold or drop samples;
  no schema change, the dated `athlete_metrics` rows are the history. The easy pace follows only as
  runs at the new ceiling enter the 28-day window, and `HR leads` until then.
- **Runs before 2026-11-02 keep the LTHR ceiling** (`GARMIN_ZONES_SINCE`), not one derived from the
  floors of that time. The engine targeted 152 then; the `HR_MAX` floors of that time would give 151,
  a ceiling that was never applied.
- **Threshold and intervals stay on LTHR (checked against past reps, 2026-11-02).** Targets 162-169 and
  162-174, green lines, `pass_hard` ceilings and the maximal-effort check are % of LTHR. On the 14 reps
  of 800-1000 m at 4:20-4:35/km (Aug-Sep), 13 peaked inside 162-174 but only 6 reached the %HRR Z5
  (172+), and none averaged there. Steady 5:40-5:50/km runs average 147-148 bpm, which is already the
  bottom of the %HRR Z4, far slower than threshold pace. No sustained threshold effort exists in the
  data, so LTHR 171 itself is unverified; a 30-minute time trial would settle it.
- **Dating.** A floors row is dated the day it syncs, and scheduled daytime runs now fetch HR zones
  (`DAY_KINDS`, one extra request each; the manual sync stays at `LIGHT_KINDS`). Left open: rows hold
  a day, not a time, so a run done earlier on the day of a change gets the new floors.
- **Display.** `time_in_zones_s` bins into the current zones (Garmin's seconds as they are when the
  floors match, older runs re-binned). The app's chip already shows the plan's `zone`; its LTHR
  percentages only serve a range without a zone from an older server.

## Per-exercise progression mode (2026-11-03)

- **Problem.** The captain's chair knee raise (bodyweight) was told "+2.5 kg": any bodyweight
  variant steps by `bodyweight_added_kg` and the double-progression rung rule moves to the next
  weight once every set is at the top of the range.
- **`Exercise.progression`: `load | reps | time`** (migration 0016, NOT NULL, default `load`).
  `load` is the unchanged behaviour. `time` is the existing timed track; `is_timed` stays (the
  installed app reads it) and always equals `progression == "time"`. `reps` never prescribes or
  steps added weight: the load stays at what was logged (0 for bodyweight), each set's target is its
  last reps + 1 capped at the top of the rep range, a miss on a censored day keeps the targets, a
  deload window halves the sets. At the top it holds and the note suggests a harder variation (the
  user's call to switch exercise, e.g. hanging leg raise); no automatic swap. Reps exercises are
  skipped by the multi-lift stall and fatigue detection, like timed ones, so a capped knee raise
  cannot trigger a deload of the other lifts.
- **Rep range.** `strength.reps_rep_range: [10, 20]` is the default for `reps` exercises
  (a program line's or `exercise_overrides` `rep_range` wins). It lives in the config defaults, not
  in the seed `program.yaml`, because the live program file is a copy owned by the user.
- **Backfill.** `time` where `is_timed`; `reps` for slug `knee_raise`; everything else `load`.
  The catalog (`progression: reps` in exercises.yaml) sets it for new databases.
- **API.** `POST /exercises` takes optional `progression` next to `timed` (contradiction: 422;
  `timed: true` alone = `time`). `PATCH /exercises/{id}` takes `load | reps` and marks
  `edited_by_user` (the catalog sync then keeps the choice). Switching to or from `time` is 422:
  timed exercises log durations, so it would break their history.
- **Open.** A `reps` exercise has no e1RM-based stall ladder, so a plateau below the cap is not
  flagged; the coach sees the plan note only at the cap.


## Withings weigh-ins (2026-11-03)

- **What.** A Withings scale's weigh-ins import into `body_measurements` (`source=withings`,
  `note="withings"`) through the Withings Public API (OAuth 2.0, scope `user.metrics`, `getmeas`
  meastype 1). Package `apps/fitness/withings/` (client, token file, state, sync, service), job
  `jobs/withings_jobs.py`, endpoints in docs/api/sync.md "Withings". Rows reach the app through the
  normal sync pull; nothing in the pull contract changed.
- **Inert when unconfigured.** `configured` = `WITHINGS_CLIENT_ID` + `WITHINGS_CLIENT_SECRET` +
  `WITHINGS_REDIRECT_URI`. Without them `status` says `configured: false`, the other endpoints are
  `503`, no job is scheduled and nothing contacts Withings. Setup: create a Public API app at
  developer.withings.com, register the redirect URI
  `https://<domain>/api/v1/fitness/withings/callback` exactly, put the three values in
  `backend/.env.production`, `deploy/push.sh --with-env`, then connect from the app or
  `cli withings connect`.
- **Token file, not the database.** `var/withings_token.json` (mode 0600, atomic write, like
  `garmin_token.json`) keeps userid, access token, refresh token, expiry. Refresh tokens rotate:
  every refresh saves the new pair before the access token is used, under a process lock so two
  threads cannot invalidate each other. The access token is refreshed when under 5 minutes are
  left, or once after a rejection. The file keeps secrets out of DB snapshots (`pull-backup.sh`)
  and the app sync, and "disconnect" is deleting it. Disconnect and revocation leave imported
  rows and `sync_state` alone; a revoked refresh token deletes the file and records the error.
- **Stateless OAuth state.** `{issued_at}.{app|web}.{nonce}.{mac}`, HMAC-SHA256 keyed with the
  client secret, valid 10 minutes, constant-time compare. No server-side storage; the `client`
  field tells the public callback whether to redirect into the iOS app or show a page. A bad state
  never reaches the code exchange.
- **Polling schedule and overlap.** Cron at `WITHINGS_SYNC_TIMES` (default 06:15, 09:15, 13:15,
  20:15, scheduler tz) plus `POST /withings/sync`. Each run asks for groups modified since the last
  success minus 1 day (first run: `WITHINGS_BACKFILL_DAYS`, 365), so a missed run or an edited
  weigh-in is picked up; unchanged rows are not touched (no pull churn). New weights rebuild the
  stats (bodyweight-loaded e1RM), like Garmin weights. One sync at a time (lock); a second caller
  gets `busy`.
- **Dedup by group id.** Row id = `uuid5(fixed namespace, "withings:<grpid>")`, so re-runs and
  edits update one row, and a row the user deleted in the app stays a tombstone
  (`upsert_measurement` never revives it). Weights outside 20-400 kg are skipped (the API's own
  bounds); `measured_at` is the group's date in UTC, `day` the local day in `USER_TZ`.
- **Coexistence with Garmin.** Both sources can log the same morning (different ids: Garmin's is
  per day, Withings' per weigh-in). The timeline's "later entry of the same day wins" applies, so
  whichever was measured last that day feeds `bodyweight_at`; nothing is merged or deleted.
- **Open.** Withings notify webhooks (push instead of polling; needs a public callback and
  subscription renewal). Deletions made on the Withings side are not propagated (`getmeas` does not
  return deleted groups); remove the entry in the app.

## Watch push window and strides (2026-11-06)
- **Window 1 day.** The push used today + 2 days, so the 05:00 push on Friday already put Sunday's
  long run on the watch, and once pushed it was kept up to date until its day. `GARMIN_PUSH_DAYS`
  now defaults to 1 (today and tomorrow; the 19:45 push puts tomorrow's run on the watch the
  evening before). Only workouts sent on request ("Send to watch" for a later day) stay beyond the
  window; they carry ` sent=app` in the marker. Our other workouts beyond the window (from the old
  2-day window) are unscheduled and deleted and pushed again when their day comes into the window.
  Only our workout definitions are touched, never activities.
- **Strides explicit.** Easy/long names end in the strides (`Wed 04.11 Easy 8 km + 6x20s strides`,
  max 40 characters now), the steps were already a repeat group. The app shows the strides on the
  run card, the week list and the run sheet (`structure.strides` of easy/long sessions).
  `BUILDER_VERSION` 2 replaces every pushed workout once.
- **No turn-around step.** A halfway split of the easy distance was tried and dropped: the strides
  come on top, so the halfway point of the distance is not the turning point of the route.

## Strides inside the run distance (2026-11-11)
- **Strides count toward the distance.** The strides (6 x 20 s + 60 s jog) were run on top of the
  easy run's distance, so the planned weekly km (and the volume cap the planner fits them under)
  missed ~1.5 km per easy run while the actual km from Garmin included them. Now an easy run's
  `distance_km` is the whole run: the planner stores `structure.strides.est_km` (strides at 0.65 x
  the easy pace, the jog at easy pace, midpoint of the easy pace range) and the watch workout's
  distance step is `distance_km - est_km` (at least 1 km), so a 5 km easy run is ~3.5 km easy +
  strides. Weekly totals and the cap therefore include the strides without any change to the
  volume logic. Sessions planned before carry no `est_km`; the watch builder and the app estimate
  it the same way.
- Watch names say `w/ 6x20s strides` instead of `+ ...` (the km are the total); `BUILDER_VERSION`
  3 replaces every pushed workout once. The app shows "After 6.5 km easy · ... · ≈ 1.5 km of the
  8 km" on the run sheet and "incl. 6 × 20 s strides" on the Today card.

## Program slots, substitutes, ad-hoc targets, stale history (2026-11-23)
- **The saved program is the default of every session.** `program.yaml` (the user's last saved
  template) is never changed by the engine. A slot whose variant is not usable at the current gym
  (no generic, no active variant there) is filled for that plan by a **substitute** of the same
  movement pattern (`engine/store/substitutes.py`): the candidate trained most recently at this
  gym, else anywhere, else the catalog (same exercise, same primary muscle, a machine at this gym,
  lowest id); never a variant already in that day; inactive variants never. It progresses from its
  own history and is marked `substitute_for` / `substitute_reason`. At a gym where the default
  exists, the default is used again. Nothing of the pattern -> the old `needs_setup` item; the app
  shows it as an open slot (set up the machine or pick an exercise) instead of dropping it.
- **Equipment is part of an exercise's identity.** A variant of the same exercise on other
  equipment than the program line names ("Lateral Raise (Cable)" for "Lateral Raise (Machine)") is
  a substitute with its own progression, never merged; the same label at another gym (per-gym
  machines) is the same exercise.
- **Templates.** A one-session swap leaves the program alone unless the user picks "update
  template". The update sends the plan's `template_sets` (base count, not the deload / comeback
  cut, which had saved 2 instead of 3 sets) and `slot` for substitutes done as planned and for open
  slots, so they keep the default; an exercise the user swapped in explicitly replaces it.
- **Ad-hoc exercises progress.** Every lifting plan carries `adhoc_targets` for recently trained
  variants usable here, computed with the same `prescribe` rules (double progression, rung rule,
  break tiers, deload windows); the app uses them when an exercise is added or swapped in, instead
  of copying the last session. One source of truth: the backend engine.
- **Stale history.** `strength.stale_gap_days` (112): exposures before a gap that long no longer
  count for the stall counter, progress events (best load / e1RM) or reference reps, and a
  stall-ladder step needs `n_required` exposures after the comeback (older persisted steps that
  stood on stale history are ignored). The comeback is handled once by `break_tiers` (e.g. -10 %,
  half the sets in the first week), then the lift progresses normally. Before, a Bicep Curl back
  after 675 days was flagged "no progress in 4+ clean sessions" from its 2024 sessions.
