#!/usr/bin/env bash
# Quick server health overview.   deploy/status.sh user@host
set -euo pipefail

HOST="${1:-}"
[[ -n "$HOST" && "$HOST" =~ ^[A-Za-z0-9._@:-]+$ && "$HOST" != -* ]] || { echo "usage: $0 user@host" >&2; exit 1; }
REMOTE_DIR=/opt/fitnessbuddy

# shellcheck disable=SC2086  # SSH_OPTS is intentionally word-split
ssh -o BatchMode=yes -o ConnectTimeout=15 ${SSH_OPTS:-} "$HOST" bash -s -- "$REMOTE_DIR" <<'REMOTE'
set -u
cd "$1/deploy" || { echo "error: $1/deploy missing (run push.sh)" >&2; exit 1; }

echo "== containers"
docker compose ps || true

echo; echo "== backend log (last 30 lines)"
docker compose logs --no-color --tail=30 backend 2>&1 || true

echo; echo "== last backup"
if [ -f /var/log/fitnessbuddy-backup.log ] && [ -s /var/log/fitnessbuddy-backup.log ]; then
  tail -n 1 /var/log/fitnessbuddy-backup.log
else
  echo "no backup log entry yet (cron runs daily at 03:15)"
fi
latest="$(ls -1t backups/fitness_*.db.gz 2>/dev/null | head -n1 || true)"
[ -z "$latest" ] || echo "newest file: $latest ($(du -h "$latest" | cut -f1))"

echo; echo "== disk"
df -h / | tail -n +1
du -sh data backups 2>/dev/null || true
echo; docker system df 2>/dev/null || true
REMOTE
