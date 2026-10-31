#!/bin/sh
# Migrate both databases (fitness.db and receipts.db, each with its own Alembic setup), then serve.
# Single worker on purpose: SQLite + in-process APScheduler + the receipts extraction worker thread.
set -eu

mkdir -p /app/var/fit /app/var/garminconnect /app/var/receipts_photos
alembic -c apps/fitness/alembic.ini upgrade head
alembic -c apps/receipts/alembic.ini upgrade head
exec uvicorn server:app --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips='*' --workers 1
