# FitnessBuddy

Personal training system: strength log + running + recovery data, with a deterministic
progression engine and a Claude advisor that proposes parameter changes only (validated against an
allowlist; the engine still computes weights, reps and paces). Built in steps, see
`docs/spec/decisions.md`: 1-3 backend, Strong import, Garmin sync, training engine + plans; 4 sync
API for the iOS app (`ios/`); 5 stats API (`docs/api/stats.md`); 6 Claude advisor and coach API
(`docs/api/coach.md`); 7 missed sessions and schedule changes.

```
backend/
  server.py            FastAPI entry point (`uvicorn server:app`)
  core/                app-independent code: bearer auth, settings, scheduler helpers
  apps/fitness/        FitnessBuddy: API, models, engine, advisor, importers, Alembic, data, config
    engine/core/       pure engine: config, planner, lifting, running (no database)
    engine/store/      engine database side: loaders, persist, service
    advisor/           Claude coach: consultations, approval, triggers, rules/
    garmin/ gyms/ exercises/ bodyweight/   services per area
    api/               thin routers; deps.py (get_db, settings, error mapping)
  tests/<group>/       engine advisor garmin importers api gyms sync analytics
ios/       FitnessBuddy SwiftUI app (XcodeGen `project.yml`)
deploy/    docker-compose + Caddy + nightly SQLite backup, push/bootstrap scripts (see deploy/README.md)
docs/      spec & decisions (docs/spec/decisions.md), API contracts (docs/api/)
scripts/   test-groups.sh (changed paths -> backend test groups), test-changed.sh; used by make + CI
Makefile   named commands: `make` lists them
imports/   your raw exports + import reports (gitignored)
```

Fitness routes live under `/api/v1/...` (the installed iOS app) and, identically, under
`/api/v1/fitness/...`; `/health` is unprefixed. The SQLite file and Alembic setup are in
`backend/var/` (`fitness.db`; in the container `/app/var/...`).

The Receipts server is a separate project with its own repository and deployment (see
`deploy/README.md` section 10 for how it shares the VM's Caddy).

## Setup

```sh
brew install uv
cd backend
uv sync                       # Python 3.12 venv + deps
cp .env.example .env          # optional
uv run alembic -c apps/fitness/alembic.ini upgrade head   # creates backend/var/fitness.db
```

## Import Strong history

Put Strong CSV exports anywhere under `imports/` (e.g. `imports/strong/strong_workouts.csv`).

```sh
cd backend
uv run python -m apps.fitness.importers.strong mapping          # refresh apps/fitness/data/catalog/strong_mapping.csv
# review the mapping csv: fix rows, set status=confirmed (unmapped names are skipped)
uv run python -m apps.fitness.importers.strong import            # dry run (default), writes a report
uv run python -m apps.fitness.importers.strong import --apply    # write to the DB
```

Gym-bound variants (machine/cable/smith/plate-loaded) are split per gym by session date, see
`backend/apps/fitness/data/catalog/gyms.yaml` (rename gyms, move a boundary or add `same_as: gym1`, then
re-import: sets move, nothing is duplicated). `overrides: [{date, gym}]` pins single days to a
gym (e.g. one visit to the old gym); a gym can also be split in time ("Gym 1 (old machines)" /
"Gym 1") when the machines were replaced. Mapping rows have an `active` column (false =
no longer trained, never planned).

Reports land in `imports/reports/strong_import_<timestamp>.md`. Re-running is safe (idempotent);
newer overlapping exports replace older versions of the same workout.

## Tests, lint, CI

`make` (repo root) lists the targets; backend ones run `cd backend && uv run ...`.

```sh
make test                  # full backend suite (pytest -n auto)
make test-fast             # -m "not db": tests that need no database, a few seconds
make test-group GROUP=engine
make test-changed          # only the groups your changes touch (vs origin/main + uncommitted)
make lint                  # ruff check + ruff format --check;  make format fixes
make typecheck             # pyright (basic) on the paths in backend/pyproject.toml
make check                 # lint + typecheck + test + ios-test-fast
make ios-test-fast         # iOS Fast test plan (SIM="iPhone 17 Pro"); ios-test = Full, ios-build
```

Backend tests live in `backend/tests/<group>/` with groups `engine advisor garmin importers api
gyms sync analytics`; each test gets a marker named after its folder (`pytest -m engine` ==
`pytest tests/engine`), plus `db` when it needs the database. `scripts/test-groups.sh` maps changed
paths to groups (e.g. `engine/**` -> engine, analytics, advisor; `api/**`, `core/**`, `db/**`,
alembic, settings, `pyproject.toml`, root `conftest.py` -> all); `make test-changed` and CI both
use it.

CI (`.github/workflows/`): `backend.yml` runs lint + pyright and, on PRs, one matrix job per affected group;
every push to `main` runs the full suite (the deploy gate needs that run). `ios.yml` runs
`xcodegen` + the `Fast` plan on PRs touching `ios/**` or `docs/api/examples/**`, `Full` on `main`.
`tooling.yml` checks the shell scripts.

Pre-commit (ruff, ruff format, `bash -n`), once per clone:

```sh
brew install pre-commit && pre-commit install
```

## Deploy

`deploy/push.sh ubuntu@HOST` (or `make deploy DEPLOY_ARGS="ubuntu@HOST"`) ships the committed
`HEAD`, not the working tree, and refuses uncommitted changes under `backend/`/`deploy/` and a
`HEAD` without a green `backend` CI run (pushed, `gh auth login`). Escape hatches:
`--allow-dirty` (still ships `HEAD`), `--working-tree` (ships the working tree, emergencies),
`--skip-ci-check`; `--dry-run` prints the remote commands without running them. Runbook:
`deploy/README.md`.

## Bodyweight

```sh
cd backend
uv run python -m apps.fitness.cli bodyweight add 82 [--date 2026-10-24]   # idempotent, one entry per day
uv run python -m apps.fitness.cli bodyweight list
uv run python -m apps.fitness.cli stats rebuild                             # after changing the log
```

Fallback when the log is empty: `user.bodyweight_kg` in `apps/fitness/config/engine.v2.yaml`.

## Garmin sync (read-only towards Garmin)

Official Personal API (pull-only). Create a key at
https://connect.garmin.com/app/settings/personalApiToken (valid 30 days to 1 year), put it into
`backend/.env` as `GARMIN_PERSONAL_API_KEY=...` (never commit it). Facts, windows and quirks:
`docs/garmin-personal-api.md`.

```sh
cd backend
uv run alembic -c apps/fitness/alembic.ini upgrade head
uv run python -m apps.fitness.cli garmin check                    # token exchange + one probe per data kind
uv run python -m apps.fitness.cli garmin backfill [--since 2023-01-01] [--kinds activities,sleep]
uv run python -m apps.fitness.cli garmin sync                     # incremental (cursor - 3 days overlap)
uv run python -m apps.fitness.cli garmin reparse                  # re-run parsers over stored raw JSON
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

## Training engine (step 3)

Deterministic, parameterised by the versioned config, every decision logged (`decisions_log`).

```sh
cd backend
uv run alembic -c apps/fitness/alembic.ini upgrade head
uv run python -m apps.fitness.cli engine program generate        # apps/fitness/data/program/program.yaml from the last 8 weeks (edit it!)
uv run python -m apps.fitness.cli engine plan --date 2026-10-26 --week   # dry run (default): plan + decisions, writes nothing
uv run python -m apps.fitness.cli engine run                      # apply: reconcile, plan this + next week, decisions, triggers
uv run python -m apps.fitness.cli engine readiness [--date D]
uv run python -m apps.fitness.cli engine evaluate-runs [--weeks 6]
uv run python -m apps.fitness.cli engine triggers                 # open advisor triggers
uv run python -m apps.fitness.cli engine config show|rollback VERSION|check-garmin
```

- **Config**: `apps/fitness/config/engine.v3.yaml` only seeds the `config_versions` table; afterwards the DB is the
  source of truth. Derived versions (`v3.1`, ...) carry parent, diff, reason and author
  (`engine|advisor|user`); rollback creates a new version. After a Garmin sync the hook
  (`apps/fitness/jobs/garmin_jobs.py`) creates a new version when Garmin reports a different max HR / LTHR
  ("Garmin reported LTHR 171->178 on ...") and re-runs the engine (`ENGINE_AUTO_RUN=false` to skip).
  HR zones are derived from LTHR (Friel run zones) and never stored.
- **Plans**: `planned_sessions` with a stable id per (week, template slot); regeneration is idempotent,
  `done/skipped/modified` sessions are never overwritten, an `approved` one whose content changes goes
  back to `proposed`. Today's readiness adjustments (yellow: -1 set / intervals -> easy run; red: rest
  or a short easy run) are written into that day's planned session.
- **API** (bearer token): `GET /api/v1/plan/today`, `/plan/week?start=`, `/readiness?date=`,
  `/config` (active + history), `POST /config/rollback/{version}`. Plan JSON: see `apps/fitness/api/engine.py`.
- Rules, thresholds and their reasoning: `apps/fitness/config/engine.v3.yaml` (comments) and `docs/spec/decisions.md`.

## Sync API for the iOS app (step 4)

Offline-first sync with per-record last-write-wins (client `updated_at`) and a server-side change
sequence as pull cursor (`server_seq`, stamped by SQLite triggers from migration 0005, so every
write path - import, engine, Garmin, API - is picked up). Full contract, semantics and example
payloads: `docs/api/sync.md`; real fixtures: `docs/api/examples/`
(`cd backend && uv run python -m apps.fitness.scripts.gen_api_examples` regenerates them from a copy of the DB).

```
GET  /api/v1/health                          no auth: {"status","version"}
POST /api/v1/sync/push                       sessions / sets / body_measurements -> per-record applied|stale|rejected
GET  /api/v1/sync/pull?since=<cursor>        changes incl. tombstones + catalog + planned sessions + config (paged)
GET  /api/v1/variants/{id}/history?limit=10  previous performance (last N sessions)
```

After a push that changed sessions/sets the server links the planned session (done/modified),
recomputes the stats of the touched variants and re-runs the engine in the background
(`apps/fitness/jobs/engine_jobs.py`, skipped with `ENGINE_AUTO_RUN=false`). Run `uv run alembic -c apps/fitness/alembic.ini upgrade head`
after pulling this step (adds `server_seq`, `sync_seq`, `sets.completed_at`).

## API (skeleton)

```sh
cd backend && uv run uvicorn server:app --reload
# public: GET /health, /api/v1/health (also under /api/v1/fitness/...). Everything else needs `Authorization: Bearer $API_TOKEN`
# Set DOCS_ENABLED=true in .env for /docs locally:
# GET /api/v1/sessions, /api/v1/variants, /api/v1/variants/{id}/stats
# bodyweight (needs `Authorization: Bearer $API_TOKEN`; set API_TOKEN in .env):
# GET /api/v1/bodyweight?from=&to=, POST /api/v1/bodyweight (client uuid, upsert),
# DELETE /api/v1/bodyweight/{id} (tombstone)
# Garmin data (same bearer token): GET /api/v1/runs?from=&to=&activity_type=&include_other=,
# GET /api/v1/runs/{id} (with laps), GET /api/v1/health/daily?from=&to=
```
