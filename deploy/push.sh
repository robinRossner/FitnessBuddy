#!/usr/bin/env bash
# Push code (and optionally env + DB) from the Mac to the server and (re)start the stack.
#
#   deploy/push.sh user@host [--with-env] [--with-db] [--domain example.duckdns.org] [--force]
#                  [--dry-run] [--skip-ci-check] [--allow-dirty] [--working-tree]
#
# Ships the committed HEAD (git archive of backend/ and deploy/), not the working tree. Before
# anything remote happens it refuses a dirty tree (tracked files under backend/ or deploy/) and a
# HEAD without a green `backend` CI run (gh run list --commit; HEAD must be pushed).
#
#   --with-env   upload backend/.env.production (mode 600). Required on the first push.
#   --with-db    replace the server DB with a consistent snapshot of backend/var/fitness.db and
#                upload var/garminconnect, var/fit, var/garmin_token.json. The existing server DB
#                is first backed up to deploy/backups/pre-push_<ts>.db.
#   --domain D   write DOMAIN=D into deploy/.env on the server (kept from earlier pushes if omitted).
#   --force      do not ask before replacing a server DB that has newer data than the local one.
#   --dry-run    run all local checks, print the ssh/rsync commands instead of running them.
#                The host may be omitted (a placeholder is used).
#   --skip-ci-check  do not require a green backend.yml run for HEAD (needs no gh, no push).
#   --allow-dirty    deploy despite uncommitted changes; still ships HEAD, not those changes.
#   --working-tree   emergency: ship the working tree as is (implies --allow-dirty).
#
# Env: SSH_OPTS="-i ~/.ssh/other_key" for extra ssh options.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
REMOTE_DIR=/opt/fitnessbuddy
LOCAL_DB="$ROOT/backend/var/fitness.db"
LOCAL_ENV="$ROOT/backend/.env.production"

die()  { echo "error: $*" >&2; exit 1; }
info() { printf '==> %s\n' "$*"; }
warn() { printf 'warning: %s\n' "$*" >&2; }
usage() { awk 'NR > 1 && /^#/ { sub(/^# ?/, ""); print; next } NR > 1 { exit }' "$0"; exit "${1:-1}"; }

# ---------- args ----------
HOST=""; WITH_DB=0; WITH_ENV=0; DOMAIN=""; FORCE=0
DRY_RUN=0; SKIP_CI=0; ALLOW_DIRTY=0; WORKING_TREE=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --with-db)  WITH_DB=1 ;;
    --with-env) WITH_ENV=1 ;;
    --force)    FORCE=1 ;;
    --dry-run)  DRY_RUN=1 ;;
    --skip-ci-check) SKIP_CI=1 ;;
    --allow-dirty)   ALLOW_DIRTY=1 ;;
    --working-tree)  WORKING_TREE=1; ALLOW_DIRTY=1 ;;
    --domain)   [[ $# -ge 2 ]] || die "--domain needs a value"; DOMAIN="$2"; shift ;;
    --domain=*) DOMAIN="${1#--domain=}" ;;
    -h|--help)  usage 0 ;;
    -*)         die "unknown option: $1 (see --help)" ;;
    *)          [[ -z "$HOST" ]] || die "unexpected argument: $1"; HOST="$1" ;;
  esac
  shift
done
if [[ -z "$HOST" && $DRY_RUN -eq 1 ]]; then HOST="ubuntu@HOST"; fi
[[ -n "$HOST" ]] || usage 1
[[ "$HOST" =~ ^[A-Za-z0-9._@:-]+$ && "$HOST" != -* ]] || die "invalid host: $HOST"
if [[ -n "$DOMAIN" ]]; then
  [[ "$DOMAIN" =~ ^[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?$ ]] || die "invalid domain: $DOMAIN"
fi

# ---------- local preflight ----------
command -v rsync >/dev/null || die "rsync not found"
command -v ssh   >/dev/null || die "ssh not found"
[[ -f "$ROOT/backend/Dockerfile" && -f "$ROOT/deploy/docker-compose.yml" ]] || die "run from the repo (backend/ and deploy/ missing)"
if [[ $WITH_ENV -eq 1 ]]; then
  [[ -f "$LOCAL_ENV" ]] || die "$LOCAL_ENV missing; run deploy/make-prod-env.sh first"
fi
if [[ $WITH_DB -eq 1 ]]; then
  command -v sqlite3 >/dev/null || die "sqlite3 not found locally"
  [[ -f "$LOCAL_DB" ]] || die "$LOCAL_DB missing; nothing to upload"
fi

# ---------- git gates: clean tree, green CI for HEAD ----------
command -v git >/dev/null || die "git not found"
git -C "$ROOT" rev-parse --git-dir >/dev/null 2>&1 || die "$ROOT is not a git checkout"
HEAD_SHA="$(git -C "$ROOT" rev-parse HEAD)"
info "deploying $(git -C "$ROOT" log -1 --format='%h %s' HEAD)"

dirty="$(git -C "$ROOT" status --porcelain --untracked-files=no -- backend deploy)"
if [[ -n "$dirty" ]]; then
  if [[ $ALLOW_DIRTY -eq 1 ]]; then
    if [[ $WORKING_TREE -eq 1 ]]; then
      warn "uncommitted changes under backend/ or deploy/ WILL be shipped (--working-tree):"
    else
      warn "uncommitted changes under backend/ or deploy/ are NOT shipped (HEAD is):"
    fi
    printf '%s\n' "$dirty" | sed 's/^/  /' >&2
  else
    printf '%s\n' "$dirty" >&2
    die "uncommitted changes under backend/ or deploy/ (above). Commit them, or pass --allow-dirty (ships HEAD anyway)"
  fi
fi

if [[ $SKIP_CI -eq 1 ]]; then
  warn "CI check skipped (--skip-ci-check)"
else
  info "checking CI (backend.yml) for HEAD ${HEAD_SHA:0:7}"
  [[ -n "$(git -C "$ROOT" branch -r --contains HEAD 2>/dev/null)" ]] \
    || die "HEAD ${HEAD_SHA:0:7} is not on any remote branch (git fetch if it is), so CI never ran on it: push it first, or pass --skip-ci-check"
  command -v gh >/dev/null || die "gh (GitHub CLI) not found: brew install gh && gh auth login, or pass --skip-ci-check"
  gh auth status >/dev/null 2>&1 || die "gh is not authenticated: gh auth login, or pass --skip-ci-check"
  runs="$(cd "$ROOT" && gh run list --workflow backend.yml --commit "$HEAD_SHA" \
    --json status,conclusion --jq '.[] | "\(.status) \(.conclusion)"')" \
    || die "gh run list failed (see above); pass --skip-ci-check to deploy without the CI check"
  if grep -qx 'completed success' <<<"$runs"; then
    echo "CI green for ${HEAD_SHA:0:7}"
  elif [[ -z "$runs" ]]; then
    die "no backend.yml run for ${HEAD_SHA:0:7} yet (pushed just now? wait a moment); or pass --skip-ci-check"
  elif grep -qv '^completed' <<<"$runs"; then
    die "backend.yml is still running for ${HEAD_SHA:0:7} (gh run watch); or pass --skip-ci-check"
  else
    die "backend.yml did not pass for ${HEAD_SHA:0:7} ($(sort -u <<<"$runs" | tr '\n' ' ')); fix CI or pass --skip-ci-check"
  fi
fi

WORK="$(mktemp -d)"
trap '[[ $DRY_RUN -eq 1 ]] || ssh -O exit -o ControlPath="$WORK/cm" "$HOST" 2>/dev/null || true; rm -rf "$WORK"' EXIT

# ---------- source tree ----------
if [[ $WORKING_TREE -eq 1 ]]; then
  SRC="$ROOT"
  warn "shipping the working tree (--working-tree), not HEAD"
else
  SRC="$WORK/src"
  mkdir -p "$SRC"
  git -C "$ROOT" archive --format=tar HEAD backend deploy | tar -x -C "$SRC" \
    || die "git archive of HEAD failed"
  [[ -f "$SRC/backend/Dockerfile" && -f "$SRC/deploy/docker-compose.yml" ]] \
    || die "HEAD has no backend/Dockerfile or deploy/docker-compose.yml"
  info "shipping HEAD ${HEAD_SHA:0:7} (git archive, $(find "$SRC" -type f | wc -l | tr -d ' ') files)"
fi

# --dry-run: print every remote command instead of running it (on stderr, so captures stay empty).
dry() { printf '[dry-run]' >&2; printf ' %q' "$@" >&2; printf '\n' >&2; }
run() { if [[ $DRY_RUN -eq 1 ]]; then dry "$@"; else "$@"; fi; }

# One multiplexed ssh connection for all calls (one auth, fast).
SSH_ARGS=(-o BatchMode=yes -o ConnectTimeout=15 -o ControlMaster=auto -o "ControlPath=$WORK/cm" -o ControlPersist=120)
# shellcheck disable=SC2206  # SSH_OPTS is intentionally word-split
[[ -n "${SSH_OPTS:-}" ]] && SSH_ARGS+=(${SSH_OPTS})
# shellcheck disable=SC2029  # client-side expansion of remote commands is intended
rssh() {
  if [[ $DRY_RUN -eq 1 ]]; then printf '[dry-run] ssh %s %s\n' "$HOST" "$*" >&2; return 0; fi
  ssh "${SSH_ARGS[@]}" "$HOST" "$@"
}
RSYNC_SSH="ssh ${SSH_ARGS[*]}"

# ---------- server preflight ----------
info "checking ssh + server setup ($HOST)"
rssh true 2>/dev/null || die "cannot ssh to $HOST (key auth, host reachable, port 22 open in the OCI security list?)"
rssh "command -v docker >/dev/null && docker compose version >/dev/null 2>&1 && [ -d $REMOTE_DIR ] && command -v sqlite3 >/dev/null" \
  || die "server not bootstrapped (docker/sqlite3/$REMOTE_DIR missing): scp deploy/bootstrap.sh $HOST:~ && ssh $HOST 'bash ~/bootstrap.sh'"
rssh "docker info >/dev/null 2>&1" || die "ssh user cannot talk to docker (not in the docker group yet?). Re-run bootstrap.sh, then reconnect."

# ---------- code ----------
info "creating remote directories"
rssh "mkdir -p $REMOTE_DIR/backend $REMOTE_DIR/deploy/data $REMOTE_DIR/deploy/backups"

info "rsync backend/"
run rsync -az --delete --no-owner --no-group -e "$RSYNC_SSH" \
  --exclude='/.venv/' --exclude='/var/' --exclude='/.env' --exclude='/.env.production' \
  --exclude='__pycache__/' --exclude='.pytest_cache/' --exclude='.ruff_cache/' --exclude='.mypy_cache/' \
  --exclude='.DS_Store' --exclude='*.pyc' \
  "$SRC/backend/" "$HOST:$REMOTE_DIR/backend/"

info "rsync deploy/"
run rsync -az --delete --no-owner --no-group -e "$RSYNC_SSH" \
  --exclude='/data/' --exclude='/backups/' --exclude='/.env' --exclude='/imports/' --exclude='.DS_Store' \
  "$SRC/deploy/" "$HOST:$REMOTE_DIR/deploy/"

# ---------- env ----------
if [[ $WITH_ENV -eq 1 ]]; then
  info "uploading backend/.env.production (mode 600)"
  run rsync -t --no-owner --no-group -e "$RSYNC_SSH" "$LOCAL_ENV" "$HOST:$REMOTE_DIR/backend/.env.production"
fi
rssh "test -s $REMOTE_DIR/backend/.env.production" \
  || die "no backend/.env.production on the server: run deploy/make-prod-env.sh, then push with --with-env"
rssh "chmod 600 $REMOTE_DIR/backend/.env.production"

info "writing deploy/.env on the server (DOMAIN, APP_UID, APP_GID)"
rssh "bash -s -- $(printf '%q ' "$DOMAIN" "$REMOTE_DIR/deploy/.env")" <<'REMOTE'
set -euo pipefail
domain="$1"; f="$2"
touch "$f"; chmod 600 "$f"
setkey() {  # setkey KEY VALUE: replace or append
  grep -v "^$1=" "$f" > "$f.tmp" || true
  printf '%s=%s\n' "$1" "$2" >> "$f.tmp"
  mv "$f.tmp" "$f"; chmod 600 "$f"
}
setkey APP_UID "$(id -u)"
setkey APP_GID "$(id -g)"
[ -z "$domain" ] || setkey DOMAIN "$domain"
grep -q '^DOMAIN=.' "$f" || { echo "error: DOMAIN not set; push with --domain yourname.duckdns.org" >&2; exit 1; }
REMOTE
if [[ $DRY_RUN -eq 1 ]]; then
  rssh "sed -n 's/^DOMAIN=//p' $REMOTE_DIR/deploy/.env | tail -n1"
  DOMAIN="${DOMAIN:-<DOMAIN from the server deploy/.env>}"
else
  DOMAIN="$(rssh "sed -n 's/^DOMAIN=//p' $REMOTE_DIR/deploy/.env | tail -n1")"
fi
[[ -n "$DOMAIN" ]] || die "DOMAIN missing on the server"

# ---------- database ----------
if [[ $WITH_DB -eq 1 ]]; then
  info "snapshotting local DB (sqlite .backup, safe with WAL)"
  SNAP="$WORK/fitness.db"
  sqlite3 "$LOCAL_DB" ".backup '$SNAP'"
  [[ "$(sqlite3 "$SNAP" 'PRAGMA integrity_check;')" == ok ]] || die "local snapshot failed integrity_check"
  local_seq="$(sqlite3 "$SNAP" 'select coalesce(max(seq),0) from sync_seq;' 2>/dev/null || echo unknown)"
  local_mtime="$(stat -f %m "$LOCAL_DB" 2>/dev/null || stat -c %Y "$LOCAL_DB")"

  if [[ $DRY_RUN -eq 1 ]]; then
    info "[dry-run] would compare max(sync_seq.seq) with the server DB and ask before replacing newer data"
  elif rssh "test -f $REMOTE_DIR/deploy/data/fitness.db"; then
    remote_seq="$(rssh "sqlite3 -readonly $REMOTE_DIR/deploy/data/fitness.db 'select coalesce(max(seq),0) from sync_seq;'" 2>/dev/null || echo unknown)"
    remote_mtime="$(rssh "stat -c %Y $REMOTE_DIR/deploy/data/fitness.db")"
    newer=0; why=""
    if [[ "$local_seq" =~ ^[0-9]+$ && "$remote_seq" =~ ^[0-9]+$ ]]; then
      if (( remote_seq > local_seq )); then newer=1; why="server max(seq)=$remote_seq > local $local_seq"; fi
    elif (( remote_mtime > local_mtime )); then
      newer=1; why="server DB mtime is newer than the local one (seq unavailable)"
    fi
    if [[ $newer -eq 1 ]]; then
      warn "the server DB has newer data than your local DB ($why)."
      warn "Pushing will replace it (a backup is kept in deploy/backups/pre-push_*.db)."
      if [[ $FORCE -ne 1 ]]; then
        [[ -t 0 ]] || die "refusing without a terminal; re-run with --force to overwrite"
        read -r -p "Overwrite the server DB? [y/N] " ans </dev/tty
        [[ "$ans" == y || "$ans" == Y ]] || die "aborted, server DB untouched"
      fi
    fi
  else
    info "no DB on the server yet"
  fi

  ts="$(date +%Y%m%d_%H%M%S)"
  info "stopping backend, backing up existing server DB"
  rssh "bash -s -- $(printf '%q ' "$REMOTE_DIR" "$ts")" <<'REMOTE'
set -euo pipefail
cd "$1/deploy"
docker compose stop backend >/dev/null 2>&1 || true
mkdir -p backups
if [ -f data/fitness.db ]; then
  sqlite3 data/fitness.db ".backup 'backups/pre-push_$2.db'"
  echo "server DB saved to deploy/backups/pre-push_$2.db"
fi
REMOTE

  info "uploading DB snapshot + Garmin state"
  run rsync -t --no-owner --no-group -e "$RSYNC_SSH" "$SNAP" "$HOST:$REMOTE_DIR/deploy/data/fitness.db.new"
  rssh "cd $REMOTE_DIR/deploy/data && rm -f fitness.db-wal fitness.db-shm && mv fitness.db.new fitness.db"
  for d in garminconnect fit; do
    if [[ -d "$ROOT/backend/var/$d" ]]; then
      rssh "mkdir -p $REMOTE_DIR/deploy/data/$d"
      run rsync -a --no-owner --no-group -e "$RSYNC_SSH" --exclude='.DS_Store' \
        "$ROOT/backend/var/$d/" "$HOST:$REMOTE_DIR/deploy/data/$d/"
    else
      warn "backend/var/$d not found locally, skipped"
    fi
  done
  if [[ -f "$ROOT/backend/var/garmin_token.json" ]]; then
    run rsync -t --no-owner --no-group -e "$RSYNC_SSH" \
      "$ROOT/backend/var/garmin_token.json" "$HOST:$REMOTE_DIR/deploy/data/garmin_token.json"
  fi
fi

rssh "chmod 600 $REMOTE_DIR/deploy/data/garmin_token.json 2>/dev/null || true"

# Container runs as the ssh user's uid/gid (APP_UID/APP_GID); repair anything root-owned.
rssh "sudo -n chown -R \$(id -u):\$(id -g) $REMOTE_DIR/deploy/data $REMOTE_DIR/deploy/backups" \
  || warn "could not chown deploy/data (sudo -n failed); permissions may be wrong"

# ---------- Caddyfile check ----------
# Caddy must not be left running a config that fails to load. `validate` does no DNS lookups, so it
# passes even while the receipts-backend upstream is down.
info "validating deploy/Caddyfile with the caddy image"
rssh "cd $REMOTE_DIR/deploy && docker run --rm -e DOMAIN=$(printf '%q' "$DOMAIN") -v \"\$PWD/Caddyfile\":/etc/caddy/Caddyfile:ro caddy:2.10 caddy validate --config /etc/caddy/Caddyfile" >/dev/null \
  || die "deploy/Caddyfile does not validate; nothing was restarted"

# ---------- start ----------
info "docker compose up -d --build (first build on ARM takes a few minutes)"
rssh "cd $REMOTE_DIR/deploy && docker compose up -d --build" || die "docker compose up failed"

# rsync replaces files by rename, and a single-file bind mount keeps pointing at the OLD file, so
# compose does not notice a changed Caddyfile. Recreate only caddy when the container's copy
# differs (caddy_data keeps the certificates).
host_sum="$(rssh "sha256sum $REMOTE_DIR/deploy/Caddyfile" | cut -d' ' -f1)"
ctr_sum="$(rssh "cd $REMOTE_DIR/deploy && docker compose exec -T caddy sha256sum /etc/caddy/Caddyfile" 2>/dev/null | cut -d' ' -f1 || true)"
if [[ -n "$host_sum" && "$host_sum" != "$ctr_sum" ]]; then
  info "Caddyfile changed: recreating the caddy container (certificates stay in the caddy_data volume)"
  rssh "cd $REMOTE_DIR/deploy && docker compose up -d --force-recreate --no-deps caddy" || die "could not recreate caddy"
fi

if [[ $DRY_RUN -eq 1 ]]; then
  info "[dry-run] would wait for backend health and check https://$DOMAIN/health"
  info "dry run done: local checks passed, nothing was run on $HOST"
  exit 0
fi

info "waiting for backend health"
healthy=0
for _ in $(seq 1 60); do
  st="$(rssh "cd $REMOTE_DIR/deploy && docker inspect -f '{{.State.Health.Status}}' \$(docker compose ps -q backend)" 2>/dev/null || echo unknown)"
  if [[ "$st" == healthy ]]; then healthy=1; break; fi
  sleep 3
done
if [[ $healthy -ne 1 ]]; then
  warn "backend not healthy after 3 min (status: ${st:-unknown}). Last logs:"
  rssh "cd $REMOTE_DIR/deploy && docker compose logs --tail=40 backend" >&2 || true
  exit 1
fi
echo "backend healthy"

# ---------- public check ----------
info "checking https://$DOMAIN/health"
ok=0
for _ in 1 2 3 4 5 6; do
  if body="$(curl -fsS --max-time 10 "https://$DOMAIN/health" 2>/dev/null)"; then ok=1; break; fi
  sleep 5
done
if [[ $ok -eq 1 ]]; then
  echo "OK: $body"
else
  warn "https://$DOMAIN/health not reachable yet. Likely causes:"
  warn "  - DNS: $DOMAIN must point to the VM's public IP (dig +short $DOMAIN)"
  warn "  - OCI security list must allow ingress TCP 80/443"
  warn "  - Let's Encrypt cert issuance still running: ./deploy/status.sh $HOST, or"
  warn "    ssh $HOST 'cd $REMOTE_DIR/deploy && docker compose logs --tail=50 caddy'"
fi
info "done"
