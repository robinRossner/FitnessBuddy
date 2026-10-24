#!/usr/bin/env bash
# Nightly consistent SQLite snapshot (safe while the app runs in WAL mode).
# Install on the host, e.g. crontab -e:
#   15 3 * * * /opt/fitnessbuddy/deploy/backup.sh >> /var/log/fitnessbuddy-backup.log 2>&1
# Requires the sqlite3 CLI on the host (apt install sqlite3).
set -euo pipefail

DEPLOY_DIR="$(cd "$(dirname "$0")" && pwd)"
DB="${DB:-$DEPLOY_DIR/data/fitness.db}"
BACKUP_DIR="${BACKUP_DIR:-$DEPLOY_DIR/backups}"
KEEP_DAYS="${KEEP_DAYS:-30}"

mkdir -p "$BACKUP_DIR"
stamp="$(date +%Y%m%d_%H%M%S)"
out="$BACKUP_DIR/fitness_$stamp.db"

sqlite3 "$DB" ".backup '$out'"
sqlite3 "$out" "PRAGMA integrity_check;" | grep -qx ok
gzip "$out"
find "$BACKUP_DIR" -name 'fitness_*.db.gz' -mtime +"$KEEP_DAYS" -delete
echo "$(date -Is) backup ok: $out.gz"
