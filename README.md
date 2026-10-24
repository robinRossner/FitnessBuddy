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

## API (skeleton)

```sh
cd backend && uv run uvicorn app.main:app --reload
# GET /health, /api/v1/sessions, /api/v1/variants, /api/v1/variants/{id}/stats
# bodyweight (needs `Authorization: Bearer $API_TOKEN`; set API_TOKEN in .env):
# GET /api/v1/bodyweight?from=&to=, POST /api/v1/bodyweight (client uuid, upsert),
# DELETE /api/v1/bodyweight/{id} (tombstone)
```
