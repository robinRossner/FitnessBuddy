# Deploy runbook (Oracle Cloud Always Free ARM VM)

Stack: FastAPI backend (single uvicorn worker, SQLite, in-process scheduler) + Caddy (automatic
HTTPS) in Docker Compose. Code goes from the Mac to the VM with rsync (`push.sh` syncs `backend/` only: `server.py`,
`core/`, `apps/`; `ios/` is never deployed); the server needs no git
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
deploy/push.sh ubuntu@HOST          # code only: rsync, rebuild, migrate (the container entrypoint runs `alembic -c apps/fitness/alembic.ini upgrade head`), health check
```

`push.sh` ships the committed `HEAD` (`git archive HEAD backend deploy` into a temp dir), not the
working tree; `backend/.env.production` and the `--with-db` files still come from the working tree.
Before anything remote happens it checks:

- **Clean tree**: tracked changes under `backend/` or `deploy/` stop it (untracked files don't).
  `--allow-dirty` goes ahead but still ships `HEAD`; `--working-tree` (emergencies) ships the
  working tree as before and implies `--allow-dirty`.
- **Green CI**: `HEAD` must be on a remote branch and have a successful `backend` workflow run
  (`gh run list --workflow backend.yml --commit HEAD`; needs `gh auth login`). Every push to
  `main` runs the full backend suite. `--skip-ci-check` skips this (no `gh`, unpushed commit).
- `--dry-run` runs the local checks and prints the ssh/rsync commands without running any of
  them (the host may be omitted): `deploy/push.sh --dry-run`. `make deploy DEPLOY_ARGS="..."`
  wraps the script.

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

The bearer token is `API_TOKEN` (the name stays so the installed iPhone keeps working).
`make-prod-env.sh` keeps an existing token and generates one when it is missing.

```sh
# delete the API_TOKEN line, keep everything else
sed -i '' '/^API_TOKEN=/d' backend/.env.production && deploy/make-prod-env.sh
deploy/push.sh ubuntu@HOST --with-env                      # restarts the backend with the new token
deploy/copy-token.sh                                        # paste into the iPhone app
```

(Editing the token by hand in `.env.production` also works. `copy-token.sh` copies it to the
clipboard without printing it.) A token that is unset on the server makes the endpoints answer 503.

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

## 10. Caddy

`deploy/Caddyfile` sends everything to `backend:8000`.

- The compose project name is pinned (`name: deploy` in `docker-compose.yml`), so the network
  `deploy_default` and the `caddy_data` volume (the certificates) keep their names.
  Check on the VM: `docker compose ls`, `docker network ls | grep deploy_default`,
  `docker volume ls | grep caddy_data` (expect `deploy_caddy_data`).
- `push.sh` validates the Caddyfile with the caddy image before restarting anything, and recreates
  only the caddy container when its Caddyfile changed (a rsync-replaced single-file bind mount is
  otherwise not noticed by compose).
