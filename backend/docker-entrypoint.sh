#!/bin/sh
# Migrate fitness.db, then serve.
# Single worker on purpose: SQLite + in-process APScheduler.
set -eu

mkdir -p /app/var/fit /app/var/garminconnect
alembic -c apps/fitness/alembic.ini upgrade head
exec uvicorn server:app --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips='*' --workers 1
