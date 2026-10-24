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

Reports land in `imports/reports/strong_import_<timestamp>.md`. Re-running is safe (idempotent);
newer overlapping exports replace older versions of the same workout.

## Tests / lint

```sh
cd backend
uv run pytest
uv run ruff check . && uv run ruff format --check .
```

## API (skeleton)

```sh
cd backend && uv run uvicorn app.main:app --reload
# GET /health, /api/v1/sessions, /api/v1/variants, /api/v1/variants/{id}/stats
```
