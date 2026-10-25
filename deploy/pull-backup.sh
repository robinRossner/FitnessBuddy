#!/usr/bin/env bash
# Offsite copy of the server database to this Mac (run by launchd, see deploy/README.md section 9).
# Takes a fresh snapshot on the server with backup.sh, downloads it, verifies it locally and
# mirrors the Garmin FIT files. Runs at most once per MIN_AGE_DAYS, so launchd can call it daily
# and at login: whenever the Mac is on, the copy is never more than about a week old.
#   bash deploy/pull-backup.sh            # weekly check (skips if a recent copy exists)
#   bash deploy/pull-backup.sh --force    # copy now
set -euo pipefail

HOST="${FB_BACKUP_HOST:?set FB_BACKUP_HOST=user@host}"
REMOTE_DIR="${FB_REMOTE_DIR:-/opt/fitnessbuddy/deploy}"
DEST="${FB_BACKUP_DEST:-$HOME/Backups/FitnessBuddy}"
KEEP="${FB_BACKUP_KEEP:-12}"            # weekly copies to keep
MIN_AGE_DAYS="${FB_BACKUP_MIN_AGE_DAYS:-7}"
SSH=(ssh -o BatchMode=yes -o ConnectTimeout=20)

log() { echo "$(date '+%Y-%m-%dT%H:%M:%S') $*"; }
notify() { osascript -e "display notification \"$1\" with title \"FitnessBuddy backup\"" >/dev/null 2>&1 || true; }
fail() { log "FAILED: $*"; notify "Backup failed: $*"; exit 1; }

mkdir -p "$DEST/db" "$DEST/fit"

if [[ "${1:-}" != "--force" ]]; then
  newest="$(find "$DEST/db" -name 'fitness_*.db.gz' -mtime -"$MIN_AGE_DAYS" | head -1)"
  if [[ -n "$newest" ]]; then
    log "skip: recent copy exists ($(basename "$newest"))"
    exit 0
  fi
fi

# Fresh, integrity-checked snapshot on the server; backup.sh prints "... backup ok: <path>".
out="$("${SSH[@]}" "$HOST" "$REMOTE_DIR/backup.sh" 2>&1)" || fail "server snapshot ($out)"
remote_file="$(sed -n 's/.*backup ok: //p' <<<"$out" | tail -1)"
[[ -n "$remote_file" ]] || fail "could not read snapshot path from server output"

local_file="$DEST/db/$(basename "$remote_file")"
scp -q -o BatchMode=yes "$HOST:$remote_file" "$local_file.part" || fail "download"
mv "$local_file.part" "$local_file"

# Verify the copy: gzip stream, SQLite integrity and that the history is there.
tmp="$(mktemp -t fbcheck)"
trap 'rm -f "$tmp"' EXIT
gunzip -c "$local_file" > "$tmp" || fail "gzip check"
[[ "$(sqlite3 "$tmp" 'PRAGMA integrity_check;')" == "ok" ]] || fail "integrity check"
counts="$(sqlite3 "$tmp" "select (select count(*) from sessions) || ' sessions, ' || (select count(*) from sets) || ' sets, ' || (select count(*) from runs) || ' runs';")"

# Raw Garmin FIT files (re-fetchable from Garmin, but cheap to keep).
rsync -a -e "ssh -o BatchMode=yes" "$HOST:$REMOTE_DIR/data/fit/" "$DEST/fit/" || log "warning: FIT mirror failed"

# Keep the newest $KEEP copies.
ls -1t "$DEST"/db/fitness_*.db.gz | tail -n +"$((KEEP + 1))" | while read -r old; do rm -f "$old"; done

log "ok: $(basename "$local_file") ($(du -h "$local_file" | cut -f1)), $counts"
notify "Saved $(basename "$local_file"): $counts"
