# Deploy runbook (Oracle Cloud Always Free ARM VM)

Stack: FastAPI backend (single uvicorn worker, SQLite, in-process scheduler) + Caddy (automatic
HTTPS) in Docker Compose. Code goes from the Mac to the VM with rsync; the server needs no git
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
deploy/push.sh ubuntu@HOST          # code only: rsync, rebuild, migrate (alembic upgrade head), health check
```

Server data is never touched without `--with-db` / `--with-env`. `--with-db` replaces the server
DB with a snapshot of `backend/var/fitness.db` (after backing up the old one to
`deploy/backups/pre-push_<ts>.db`) and asks first if the server DB has newer data
(`max(sync_seq.seq)`); `--force` skips the question. Do not use `--with-db` once the iPhone has
written data you want to keep, unless you pulled it first.

## 5. Restore from backup

Backups: `deploy/backups/fitness_<ts>.db.gz`, nightly, 30 days.

```sh
ssh ubuntu@HOST
cd /opt/fitnessbuddy/deploy
docker compose stop backend
cp data/fitness.db backups/before-restore.db        # optional safety copy
gunzip -c backups/fitness_YYYYMMDD_HHMMSS.db.gz > data/fitness.db
rm -f data/fitness.db-wal data/fitness.db-shm
docker compose up -d backend
```

Copy a backup to the Mac: `scp ubuntu@HOST:/opt/fitnessbuddy/deploy/backups/fitness_*.db.gz ~/Backups/`.
The VM disk is the only copy of server-side data, so pull one now and then.

## 6. Rotate the API token

```sh
rm backend/.env.production && deploy/make-prod-env.sh      # new API_TOKEN (all other keys re-copied from backend/.env)
deploy/push.sh ubuntu@HOST --with-env                      # restarts the backend with the new token
deploy/copy-token.sh                                        # paste into the iPhone app
```

(Editing only `API_TOKEN` by hand in `.env.production` also works; `make-prod-env.sh` keeps an
existing token.)

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
- Paths inside the container: DB `/app/var/fitness.db`, FIT files `/app/var/fit`, python-garminconnect
  tokens `/app/var/garminconnect`, Personal API token cache `/app/var/garmin_token.json`, all in `./data`.
- Logs: `ssh ubuntu@HOST 'cd /opt/fitnessbuddy/deploy && docker compose logs -f backend'`.
- One-off Strong imports: run them locally and push the DB with `--with-db`.

## 9. Offsite backup to the Mac

The nightly server backups live on the same VM as the database, so this Mac keeps an offsite copy.
`deploy/pull-backup.sh` takes a fresh snapshot on the server, downloads it to
`~/Backups/FitnessBuddy/db/`, verifies it (gzip, `PRAGMA integrity_check`, row counts), mirrors the
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
