# FitnessBuddy

Personal training system: strength log + running + recovery data, with a deterministic
progression engine and an optional Claude advisor (later steps). Step 1 = backend + Strong import.

```
backend/   FastAPI app, SQLAlchemy models, Alembic, Strong importer, tests
ios/       SwiftUI app (placeholder)
deploy/    docker-compose + Caddy + nightly SQLite backup
docs/      spec & decisions (docs/spec/decisions.md)
imports/   your raw exports + import reports (gitignored)
```

## Setup

```sh
brew install uv
cd backend
uv sync                       # Python 3.12 venv + deps
cp .env.example .env          # optional
uv run alembic upgrade head   # creates backend/var/fitness.db
```

## Import Strong history

Put Strong CSV exports anywhere under `imports/` (e.g. `imports/strong/strong_workouts.csv`).

```sh
cd backend
uv run python -m app.importers.strong mapping          # refresh data/catalog/strong_mapping.csv
# review the mapping csv: fix rows, set status=confirmed (unmapped names are skipped)
uv run python -m app.importers.strong import            # dry run (default), writes a report
uv run python -m app.importers.strong import --apply    # write to the DB
```

Gym-bound variants (machine/cable/smith/plate-loaded) are split per gym by session date, see
`backend/data/catalog/gyms.yaml` (rename gyms, move a boundary or add `same_as: gym1`, then
re-import: sets move, nothing is duplicated). Mapping rows have an `active` column (false =
no longer trained, never planned).

Reports land in `imports/reports/strong_import_<timestamp>.md`. Re-running is safe (idempotent);
newer overlapping exports replace older versions of the same workout.

## Tests / lint

```sh
cd backend
uv run pytest
uv run ruff check . && uv run ruff format --check .
```

## Bodyweight

```sh
cd backend
uv run python -m app.cli bodyweight add 82 [--date 2026-10-24]   # idempotent, one entry per day
uv run python -m app.cli bodyweight list
uv run python -m app.cli stats rebuild                             # after changing the log
```

Fallback when the log is empty: `user.bodyweight_kg` in `config/engine.v2.yaml`.

## Garmin sync (read-only towards Garmin)

Official Personal API (pull-only). Create a key at
https://connect.garmin.com/app/settings/personalApiToken (valid 30 days to 1 year), put it into
`backend/.env` as `GARMIN_PERSONAL_API_KEY=...` (never commit it). Facts, windows and quirks:
`docs/garmin-personal-api.md`.

```sh
cd backend
uv run alembic upgrade head
uv run python -m app.cli garmin check                    # token exchange + one probe per data kind
uv run python -m app.cli garmin backfill [--since 2023-01-01] [--kinds activities,sleep]
uv run python -m app.cli garmin sync                     # incremental (cursor - 3 days overlap)
uv run python -m app.cli garmin reparse                  # re-run parsers over stored raw JSON
```

- `GARMIN_SOURCE=personal_api|garminconnect|auto` (default `auto`): Personal API first; the
  python-garminconnect fallback (needs `GARMIN_EMAIL`/`GARMIN_PASSWORD`, logs in at most once per
  process) is used only if the key is dead or a recent window comes back empty.
  `garmin backfill --source garminconnect` pulls old history from the fallback deliberately.
- Backfill is chunked per kind window and resumable (`sync_state` cursors); a persistent 429 is
  waited out (1, 3, 10 min) and then the run stops with the cursor intact, so just re-run it.
- Stored: all activities in `runs` (`activity_type` = Garmin typeKey) + `laps`, `daily_health`,
  Garmin weights in `body_measurements` (source `garmin`), `athlete_metrics` (max HR, LTHR, zone
  floors), `garmin_raw` (raw JSON), originals in `backend/var/fit/` (all gitignored).
- Scheduler: `SCHEDULER_ENABLED=true` starts APScheduler with the API (nightly 03:30 full sync,
  four lighter runs at `SYNC_DAY_TIMES`, timezone Europe/Vienna). Off by default.

## API (skeleton)

```sh
cd backend && uv run uvicorn app.main:app --reload
# GET /health, /api/v1/sessions, /api/v1/variants, /api/v1/variants/{id}/stats
# bodyweight (needs `Authorization: Bearer $API_TOKEN`; set API_TOKEN in .env):
# GET /api/v1/bodyweight?from=&to=, POST /api/v1/bodyweight (client uuid, upsert),
# DELETE /api/v1/bodyweight/{id} (tombstone)
# Garmin data (same bearer token): GET /api/v1/runs?from=&to=&activity_type=&include_other=,
# GET /api/v1/runs/{id} (with laps), GET /api/v1/health/daily?from=&to=
```
