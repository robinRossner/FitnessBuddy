# Garmin workout push (planned runs -> watch)

The official Personal API is pull-only, so planned runs reach the watch through
python-garminconnect (`GARMIN_EMAIL` / `GARMIN_PASSWORD`, the same login and token cache as the sync
fallback). Code: `backend/apps/fitness/garmin_workouts.py`.

## What lands on the watch

Every open planned run (`easy_run`, `intervals`, `long_run`; status `proposed|approved`; no running
activity on that day yet) from **today to today + `GARMIN_PUSH_DAYS`** becomes a Garmin running
workout, **scheduled on its date** in the Garmin calendar. The watch gets it on its next sync with
the phone (Garmin Connect app), so the evening push lands before bed.

| Run | Steps |
|---|---|
| Easy / long run | one distance step with a custom HR range target (`hr.low`–`hr.high` bpm, the easy ceiling); easy runs with strides add a repeat group `reps x (stride for duration_s, recovery for recovery_s)` |
| Intervals | warm-up (distance, easy HR range) -> repeat `reps x (work: rep distance, pace range target from pace_range_s_per_km, secondary HR target from work.hr; recovery: duration_s or distance_m jog)`, last recovery skipped -> cool-down (distance) |
| Time trial | warm-up -> one work step (distance, pace target) -> cool-down |

Names are short, dated and ASCII so the watch list shows the day first: `Thu 29.10 Easy 5 km`,
`Fri 30.10 Thr 3x1k`, `Sun 01.11 Long 11 km`, `VO2 5x800m`, `5K 4x1k`, `TT 3 km` (max 32
characters).

## Ownership and cleanup (stateless)

Nothing is stored in the database. Each workout's description ends with a marker:
`[FitnessBuddy ps=<planned session id> d=<YYYY-MM-DD> h=<content hash>]`. Only workouts carrying
this marker are ever changed or deleted; any other workout in the Garmin library is left alone (a
list item without a description whose name looks like ours is fetched in full and checked first).

A push:

- uploads + schedules a missing workout;
- replaces a workout whose content, date or session changed (upload + schedule the new one first,
  then unschedule + delete the old one, so a failure never leaves the day empty);
- re-schedules our workout if it is no longer on the calendar;
- unschedules + deletes our workouts whose date is before today, whose run was done (activity on
  that day or the session closed) or whose planned session no longer exists / is no longer a run;
- keeps workouts that were pushed on request for a later day (outside the window) while their
  session stays open, and updates them when they change.

A second push with no plan change reports everything `unchanged` and writes nothing to Garmin.

## When it runs

- Scheduler (server, `SCHEDULER_ENABLED=true`): `GARMIN_PUSH_TIMES`, default **19:45** (after the
  19:30 light sync, so tomorrow morning's run is on the watch before bed) and **05:00** (after the
  03:30 nightly sync, so a readiness-driven change still lands), `SCHEDULER_TZ` (Europe/Vienna).
- After an engine re-run (iOS push, Garmin sync, coach change, gym change), in a background thread,
  only when the wanted workouts differ from the last successful push of the process (otherwise no
  Garmin request). Server process only (`SCHEDULER_ENABLED`), so local runs and tests never contact
  Garmin.
- On demand: the endpoint below (the app's "Send to watch") and
  `uv run python -m apps.fitness.cli garmin push-workouts [--dry-run] [--date YYYY-MM-DD] [--today YYYY-MM-DD]`.
  `--dry-run` prints the workouts and their JSON without contacting Garmin (no login).

Errors are logged without secrets and never break the sync or engine jobs. A failed login is
remembered for 6 hours (Garmin SSO answers repeated logins with 429).

## POST /api/v1/garmin/workouts/push

Bearer token like the other routes. Optional JSON body:

```json
{"date": "2026-11-01", "dry_run": false}
```

- `date` (optional): also push the open run of that day, even outside the window (today .. today +
  21 days; otherwise `422`).
- `dry_run` (optional): build only, Garmin is not contacted (`remote_checked: false`).

Response `200`:

```json
{
  "dry_run": false,
  "remote_checked": true,
  "window": {"from": "2026-10-28", "to": "2026-10-30"},
  "counts": {"uploaded": 1, "updated": 0, "unchanged": 1, "scheduled": 0, "deleted": 1, "failed": 0},
  "days": [
    {"date": "2026-10-27", "items": [{"date": "2026-10-27", "action": "deleted",
      "name": "Tue 27.10 Easy 5 km", "planned_session_id": "…", "workout_id": 1001, "reason": "past"}]},
    {"date": "2026-10-29", "items": [{"date": "2026-10-29", "action": "uploaded",
      "name": "Thu 29.10 Easy 5 km", "planned_session_id": "…", "workout_id": 1002, "reason": null}]}
  ]
}
```

`action`: `uploaded | updated | unchanged | scheduled | deleted | failed` (`failed` carries the
error in `reason`; the old workout is kept). Errors: `401` bad token, `422` bad date, `502` Garmin
refused / failed (e.g. rate limited), `503` push disabled (`GARMIN_PUSH_WORKOUTS=false`), no Garmin
login configured, or the login failed recently.

## POST /api/v1/garmin/sync

Manual "sync now" (the app's sync button). Bearer token, no body. Synchronous: returns when the
sync (activities, daily summary, sleep, HRV; new activities also get their laps/FIT) **and** the
engine run for changed data are done, usually 5-30 s with a new activity (give the client a request
timeout of 60 s or more). Also served as `/api/v1/fitness/garmin/sync`.

Response `200`:

```json
{
  "status": "synced",
  "retry_after_s": null,
  "new_activities": 1,
  "latest_activity": {"title": "Morning Run", "activity_type": "running",
                      "started_at_local": "2026-11-01T07:12:03"},
  "hrv_today": true,
  "manual_syncs_today": 3,
  "manual_syncs_limit": 30
}
```

- `status`: `synced` | `cooldown` (last attempt less than `GARMIN_MANUAL_SYNC_COOLDOWN_S` ago) |
  `busy` (a scheduled sync is running; try again) | `limit_reached` (daily cap used up).
  Everything but `synced` makes no Garmin request.
- `retry_after_s`: seconds until it can work (`cooldown`; `limit_reached`: until the next local
  midnight of `USER_TZ`; `busy`: 5). `null` for `synced`.
- `new_activities`: activities inserted by this call (re-reading known ones does not count);
  `0` unless `synced`.
- `latest_activity`: newest non-deleted activity of any type, or `null`; always returned, as is
  `hrv_today` (today's, in `USER_TZ`, daily health has an overnight HRV value).
- `manual_syncs_today` / `manual_syncs_limit`: attempts so far today (a failed attempt counts, a
  `busy` one does not) and the cap.

Errors: `401` bad token; `429` Garmin rate limited the account (with `Retry-After` when Garmin sent
one): manual syncs then answer `429` without contacting Garmin until it has passed (15 min when
unknown); `503` no usable Garmin source (key missing/expired); `502` Garmin failed. Messages never
contain secrets.

The guard (cooldown, daily count, 429 pause) lives in memory of the single server process and resets
on restart. The login-based fallback source is restricted to its cached tokens here: it can resume
them but never does a fresh SSO login.

## Settings

| Variable | Default | Meaning |
|---|---|---|
| `GARMIN_PUSH_WORKOUTS` | `true` | master switch (scheduled jobs, hook, endpoint) |
| `GARMIN_PUSH_DAYS` | `2` | window = today + this many days ahead |
| `GARMIN_PUSH_TIMES` | `19:45,05:00` | scheduled pushes (comma list, `SCHEDULER_TZ`) |
| `GARMIN_PUSH_AFTER_ENGINE` | `true` | push after an engine re-run changed a run (server only) |
| `GARMIN_EMAIL` / `GARMIN_PASSWORD` | – | python-garminconnect login (tokens in `GARMIN_CONNECT_TOKEN_DIR`) |
| `GARMIN_REQUEST_INTERVAL_S` | `1.0` | pacing between Garmin requests |
| `GARMIN_MANUAL_SYNC_COOLDOWN_S` | `30` | minimum gap between two manual syncs |
| `GARMIN_MANUAL_SYNC_DAILY_MAX` | `30` | manual syncs per local day |
| `SYNC_DAY_TIMES` | `07:30,08:30,09:30,11:30,15:30,19:30` | scheduled light syncs (activities, daily summary, sleep, HRV) |
