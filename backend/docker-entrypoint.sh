#!/bin/sh
# Migrate, then serve. Single worker on purpose: SQLite + in-process APScheduler.
set -eu

mkdir -p /app/var/fit /app/var/garminconnect
alembic upgrade head
exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips='*' --workers 1
