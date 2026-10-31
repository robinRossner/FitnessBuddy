# Deploy runbook (Oracle Cloud Always Free ARM VM)

Stack: FastAPI backend (single uvicorn worker, SQLite, in-process scheduler) + Caddy (automatic
HTTPS) in Docker Compose. Code goes from the Mac to the VM with rsync (`push.sh` syncs `backend/` only: `server.py`,
`core/`, `apps/`; `clients/` is never deployed); the server needs no git
credentials. State lives in `/opt/fitnessbuddy/deploy/data` (DB, `fit/`, `garminconnect/`).

## 1. Oracle Cloud console (once)

1. Compute > Instances > Create: shape **VM.Standard.A1.Flex**, 2 OCPU / 12 GB, image
   **Ubuntu 24.04** (aarch64), assign a **public IPv4**, paste `~/.ssh/id_ed25519.pub`.
2. Networking > your VCN > Security Lists > Default: add **Ingress** rules, source `0.0.0.0/0`,
   TCP destination port **80** and **443** (and optionally UDP 443 for HTTP/3). Port 22 is already open.
3. Note the public IP.

"Out of host capacity" for A1 is common: retry later (early morning), try another availability
domain, or a smaller shape (1 OCPU / 6 GB is enough) and resize later. Upgrading the tenancy to
Pay As You Go (stays free within Always Free limits) greatly improves availability.

## 2. DNS (DuckDNS)

Create a subdomain at https://www.duckdns.org (e.g. `yourname.duckdns.org`) and set its IP
to the VM's public IP. Check: `dig +short yourname.duckdns.org`.

## 3. First deploy (from the Mac, repo root)

```sh
deploy/make-prod-env.sh                                  # backend/.env.production (gitignored, 600)
scp deploy/bootstrap.sh ubuntu@HOST:~ && ssh ubuntu@HOST 'bash ~/bootstrap.sh'
deploy/push.sh ubuntu@HOST --with-env --with-db --domain yourname.duckdns.org
deploy/status.sh ubuntu@HOST
deploy/copy-token.sh                                     # token -> clipboard; paste on iPhone (Universal Clipboard)
```

`bootstrap.sh` (idempotent): timezone Europe/Vienna, apt upgrade, Docker + compose plugin,
sqlite3/rsync/unattended-upgrades, iptables 80/443/udp443 (persisted in `/etc/iptables/rules.v4`),
`/opt/fitnessbuddy`, nightly backup cron 03:15 (log `/var/log/fitnessbuddy-backup.log`),
2 GB swap, SSH password login off (only if an authorized key exists). Re-login afterwards so the
`docker` group applies. In the iOS app set the server URL to `https://<domain>` and paste the token.

The first Let's Encrypt certificate needs DNS and ports 80/443 working; Caddy retries on its own.

## 4. Update later

```sh
deploy/push.sh ubuntu@HOST          # code only: rsync, rebuild, migrate (the container entrypoint runs `alembic -c apps/fitness/alembic.ini upgrade head` and `alembic -c apps/receipts/alembic.ini upgrade head`), health check
```

Server data is never touched without `--with-db` / `--with-env`. `--with-db` replaces the server
DB with a snapshot of `backend/var/fitness.db` (after backing up the old one to
`deploy/backups/pre-push_<ts>.db`) and asks first if the server DB has newer data
(`max(sync_seq.seq)`); `--force` skips the question. Do not use `--with-db` once the iPhone has
written data you want to keep, unless you pulled it first.

## 5. Restore from backup

Backups: `deploy/backups/fitness_<ts>.db.gz` and `receipts_<ts>.db.gz`, nightly, 30 days (plus the
photo mirror, see section 10).

```sh
ssh ubuntu@HOST
cd /opt/fitnessbuddy/deploy
docker compose stop backend
cp data/fitness.db backups/before-restore.db        # optional safety copy
gunzip -c backups/fitness_YYYYMMDD_HHMMSS.db.gz > data/fitness.db
rm -f data/fitness.db-wal data/fitness.db-shm
docker compose up -d backend
```

Restoring `receipts.db` works the same way (`data/receipts.db`, `receipts_*.db.gz`); see section 10
for the receipt photos that belong to it.

Copy a backup to the Mac: `scp ubuntu@HOST:/opt/fitnessbuddy/deploy/backups/fitness_*.db.gz ~/Backups/`.
The VM disk is the only copy of server-side data, so pull one now and then.

## 6. Rotate the API tokens

Each app has its own bearer token: `API_TOKEN` (FitnessBuddy; the name stays so the installed iPhone
keeps working) and `RECEIPTS_API_TOKEN` (Receipts). A token opens only its own app's routes, so
rotate them separately. `make-prod-env.sh` keeps both existing tokens and generates one that is
missing (the first run after this change adds `RECEIPTS_API_TOKEN` and leaves `API_TOKEN` alone).

```sh
# FitnessBuddy only: delete the API_TOKEN line, keep everything else (including RECEIPTS_API_TOKEN)
sed -i '' '/^API_TOKEN=/d' backend/.env.production && deploy/make-prod-env.sh
deploy/push.sh ubuntu@HOST --with-env                      # restarts the backend with the new token
deploy/copy-token.sh                                        # paste into the iPhone app

# Receipts only: same with RECEIPTS_API_TOKEN
sed -i '' '/^RECEIPTS_API_TOKEN=/d' backend/.env.production && deploy/make-prod-env.sh
deploy/push.sh ubuntu@HOST --with-env
deploy/copy-token.sh receipts                               # paste into the Receipts app

# Both at once (all other keys are re-copied from backend/.env)
rm backend/.env.production && deploy/make-prod-env.sh
```

(Editing a token by hand in `.env.production` also works. `copy-token.sh [fitness|receipts]`
copies the chosen token to the clipboard without printing it; `fitness` is the default.)
A token that is unset on the server makes that app's endpoints answer 503.

Hardening that needs no configuration:

- `/docs`, `/redoc` and `/openapi.json` answer 404; set `DOCS_ENABLED=true` in `backend/.env` for
  local development only (`make-prod-env.sh` always writes `DOCS_ENABLED=false` into
  `.env.production`, whatever `backend/.env` says).
- Wrong or missing tokens are counted per client IP in memory: after 20 failures in 10 minutes
  (`AUTH_MAX_FAILURES`, `AUTH_FAILURE_WINDOW_S`) that IP gets 429 with `Retry-After` until the
  oldest failure ages out. A valid token always works, even from a throttled IP; health checks are
  never throttled; a backend restart clears the counters. The client IP is the last
  `X-Forwarded-For` entry (the one Caddy adds) when the connection comes from a private address
  (the Caddy container; override with `TRUSTED_PROXY_IPS`, comma-separated IPs/CIDRs), otherwise the
  connection's own address, so clients cannot spoof it.

## 7. Garmin Personal API key renewal

The key expires (30 days to 1 year, depending on what Garmin issued). Create a new one at
https://connect.garmin.com/app/settings/personalApiToken, then:

```sh
# edit GARMIN_PERSONAL_API_KEY in backend/.env, then
deploy/make-prod-env.sh && deploy/push.sh ubuntu@HOST --with-env
```

(`make-prod-env.sh` re-syncs all keys from `backend/.env` and keeps the production token.)

## 8. Claude advisor on your subscription (instead of an API key)

The advisor can run on your Claude Pro/Max subscription instead of a pay-per-token API key: the
backend image contains the Claude Code CLI (pinned version, SHA-256 checked at build time) and runs
it headless (`claude -p`, no tools, one turn, empty temp directory) with a long-lived OAuth token.

1. On your Mac, once (interactive browser login, you must do this yourself):
   ```sh
   claude setup-token        # prints a token; it is not saved anywhere
   ```
2. Put it into `backend/.env` (or `backend/.env.production`), never into git or chat:
   ```
   CLAUDE_CODE_OAUTH_TOKEN=<the token>
   ```
   `make-prod-env.sh` copies it from `backend/.env`; skip that script if you edit
   `.env.production` directly.
3. Deploy the env file and rebuild: `bash deploy/push.sh ubuntu@HOST --with-env`.
4. Check: `GET /api/v1/coach/settings` reports the key as configured; ask the coach a question.

Notes:
- `ADVISOR_PROVIDER=auto` (default) uses the token if set, else `ANTHROPIC_API_KEY`, else the
  consultation waits as `awaiting_key`. Force one with `ADVISOR_PROVIDER=claude_code|api`.
- The process never receives `ANTHROPIC_API_KEY` (it would outrank the token and bill the API).
- The token is valid for about one year; when it expires or is revoked, consultations wait as
  `awaiting_key` with a message to run `claude setup-token` again.
- Usage counts against your subscription's limits, shared with your own Claude and Claude Code use
  (a weekly review is one request). Hitting the limit fails the consultation with "Subscription
  usage limit reached, retry later"; the next daily run tries again.
- The stored cost of a consultation is only an API-price estimate: you pay nothing extra per token.
- Upgrading the CLI: bump `CLAUDE_CODE_VERSION` and both checksums in `backend/Dockerfile`
  (`https://downloads.claude.ai/claude-code-releases/<version>/manifest.json`).

## Notes

- Container runs as the ssh user's uid (`APP_UID`/`APP_GID` in `deploy/.env`, written by
  `push.sh`), so the host backup cron can read the WAL database.
- Paths inside the container: DBs `/app/var/fitness.db` and `/app/var/receipts.db`, receipt photos
  not yet archived `/app/var/receipts_photos`, FIT files `/app/var/fit`, python-garminconnect
  tokens `/app/var/garminconnect`, Personal API token cache `/app/var/garmin_token.json`, all in `./data`.
- Logs: `ssh ubuntu@HOST 'cd /opt/fitnessbuddy/deploy && docker compose logs -f backend'`.
- One-off Strong imports: run them locally and push the DB with `--with-db`.

## 9. Offsite backup to the Mac

The nightly server backups live on the same VM as the database, so this Mac keeps an offsite copy.
`deploy/pull-backup.sh` takes a fresh snapshot on the server, downloads it to
`~/Backups/FitnessBuddy/db/`, verifies it (gzip, `PRAGMA integrity_check`, row counts), does the same
for the receipts database (`receipts_db/`) and mirrors the not-yet-archived receipt photos
(`receipts_photos_pending/`, an `rsync --delete` mirror), mirrors the
Garmin FIT files to `~/Backups/FitnessBuddy/fit/`, keeps the newest 12 copies and shows a macOS
notification. A launchd agent runs it at login and daily; the script only copies when the newest
local copy is older than 7 days, so the copy is at most about a week old whenever the Mac is on.

```bash
mkdir -p ~/Library/Logs/FitnessBuddy
cp deploy/com.robinrossner.fitnessbuddy.backup.plist ~/Library/LaunchAgents/
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.robinrossner.fitnessbuddy.backup.plist
bash deploy/pull-backup.sh --force      # copy now
tail ~/Library/Logs/FitnessBuddy/backup.log
```

Restore from a Mac copy: `gunzip -c ~/Backups/FitnessBuddy/db/fitness_<stamp>.db.gz > /tmp/fitness.db`,
then follow "Restore from backup" above with that file. Remove the job with
`launchctl bootout gui/$(id -u)/com.robinrossner.fitnessbuddy.backup`.

## 10. Receipts app (receipts.db, photos, archive, Mac data)

The same container serves the Receipts app (`/api/v1/receipts/...`, contract in
`docs/api/receipts.md`). Its data sits next to the fitness data in `deploy/data`:

| What | Path on the host | In the container |
|---|---|---|
| database | `data/receipts.db` | `/app/var/receipts.db` |
| photos **not yet archived** | `data/receipts_photos/<xx>/<uuid>.jpg` | `/app/var/receipts_photos` |

Configuration (all optional except the token; `backend/.env`, see `backend/.env.example`):
`RECEIPTS_API_TOKEN` (made by `make-prod-env.sh`), `RECEIPTS_LLM_PROVIDER=auto|claude_code|api`
(uses `CLAUDE_CODE_OAUTH_TOKEN`, i.e. the subscription, if set, else `ANTHROPIC_API_KEY`; without
either, uploads are stored and extraction waits), `RECEIPTS_LLM_MODEL` (default
`claude-haiku-5-5`, the model the Mac app uses; change it here, e.g. `claude-opus-5-5`),
`RECEIPTS_MAX_PHOTO_BYTES`, `RECEIPTS_ARCHIVE_REMINDER_DAYS` (30), `RECEIPTS_DISK_WARNING_FREE_MB`
(2048), `RECEIPTS_NOTIFY_URL`. The extraction runs inside the single uvicorn worker (one photo at a
time, the queue is in `receipts.db`, a restart re-queues interrupted jobs). The `claude -p` call
never receives `ANTHROPIC_API_KEY`, like the advisor.

### Photo retention: the laptop is the archive

A receipt photo stays on the server only until the Mac archive client has stored a verified copy
and confirmed its sha256 (`POST /receipts/{id}/archive-confirm`); only that confirmation deletes
the file. **Nothing deletes a photo because of its age.** Until then the server holds the only
copy besides the phone's.

- `GET /api/v1/receipts/status` shows `unarchived_count`, `unarchived_bytes`, free disk,
  `archive_overdue` (no archive sync for 30 days while photos wait) and `disk_warning`
  (< 2 GB free). The clients show these.
- A daily job (09:00) sends a reminder when either flag is set, at most every 3 days. **Delivery
  needs `RECEIPTS_NOTIFY_URL`** (install the ntfy app on the phone, subscribe to a hard-to-guess
  topic, set `RECEIPTS_NOTIFY_URL=https://ntfy.sh/<topic>`). The backend has no other notification
  channel (`apps/fitness/notify` is empty), so without it the reminder is only a line in
  `docker compose logs backend` and the status flags are the only signal.

### Backup of receipts

`deploy/backup.sh` (03:15) now also writes `backups/receipts_<ts>.db.gz` (integrity checked, 30
days) and refreshes **one** mirror, `backups/receipts_photos_pending/`, with `rsync -a --delete`
of `data/receipts_photos`. Why a mirror and not dated copies: the photos folder contains exactly the
photos that are not archived yet, so a mirror that follows deletions is as large as the photos
themselves and can never bring back a photo the laptop already archived, whereas dated copies would
grow without bound and keep archived photos for 30 days. The script refuses (exit 1, a line in the
backup log) when the disk could not hold the mirror. `deploy/pull-backup.sh` pulls the receipts
snapshot and the mirror to the Mac as well, so unarchived photos have an offsite copy too (the
Mac mirror loses a photo at the next pull after it was archived).

Gap, stated plainly: between two nightly backups a freshly uploaded photo exists once on the
server (and on the phone that took it). Archived photos are not in any server backup by design:
the laptop archive is their only copy, so keep the Mac archive folder in Time Machine / your own
backup.

Restore after a lost or damaged server: restore `receipts.db` as in section 5, then put the photos
back without overwriting anything newer:

```sh
cd /opt/fitnessbuddy/deploy
docker compose stop backend
gunzip -c backups/receipts_YYYYMMDD_HHMMSS.db.gz > data/receipts.db && rm -f data/receipts.db-wal data/receipts.db-shm
rsync -a --ignore-existing backups/receipts_photos_pending/ data/receipts_photos/
docker compose up -d backend      # the daily job removes files of receipts that are archived in this DB
```

A receipt whose photo was archived after the snapshot was taken may point at a file that is gone:
the laptop holds it, and `archive-confirm` for it is then a no-op.

### Migrating the Mac app's data (one-off)

`python -m apps.receipts.migrate_mac_data --db <Mac receipts.db> --archive <receipts_archive/>`
is a dry run (prints what would be added, writes nothing); add `--apply` to write. It gives every
Mac row a stable UUID, is idempotent (rows already there are left alone, so a rerun never undoes
later edits), marks receipts whose photo it finds under `--archive` as `archived` with the file's
sha256 and does **not** upload any photo. It reads only the two paths you give it: work on a copy
of the Mac data, not on `~/Library/Application Support/ReceiptsApp/` while the app runs.

```sh
# on the Mac, in backend/ (writes the local backend/var/receipts.db; dry run first)
uv run python -m apps.receipts.migrate_mac_data --db ~/Desktop/mac-copy/receipts.db --archive ~/Desktop/mac-copy/receipts_archive
uv run python -m apps.receipts.migrate_mac_data --db ... --archive ... --apply
# first seed of the server (only while the server has no receipts yet: it REPLACES data/receipts.db)
ssh ubuntu@HOST 'cd /opt/fitnessbuddy/deploy && docker compose stop backend'
sqlite3 backend/var/receipts.db ".backup /tmp/receipts-seed.db"
scp /tmp/receipts-seed.db ubuntu@HOST:/opt/fitnessbuddy/deploy/data/receipts.db
ssh ubuntu@HOST 'cd /opt/fitnessbuddy/deploy && docker compose up -d backend'
```

Once phones have uploaded receipts, do not replace the server file again: copy the Mac database
(and a folder with the archive files you want hashed) to `deploy/data/mac-import/` and run the same
command inside the container instead
(`docker compose exec backend python -m apps.receipts.migrate_mac_data --db /app/var/mac-import/receipts.db --archive /app/var/mac-import/receipts_archive --apply`).
