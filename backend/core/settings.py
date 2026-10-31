"""Settings every app shares, loaded from the environment / `.env` via pydantic-settings.

An app extends `CoreSettings` with its own fields (see apps/fitness/settings.py); the env var
names are the field names upper-cased, so moving a field between core and an app never changes
the deployment's `.env`. Secrets are `SecretStr` so they never end up in logs or reprs.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[1]  # backend/: var/ (state), .env, Dockerfile
REPO_ROOT = BACKEND_DIR.parent


class CoreSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(BACKEND_DIR / ".env", REPO_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # One bearer token per app (core/auth.py). `api_token` is the fitness app's (the name stays so
    # the installed iPhone app and existing .env.production files keep working); the receipts
    # router accepts only `receipts_api_token`. Unset => that app's endpoints answer 503.
    api_token: SecretStr | None = None
    receipts_api_token: SecretStr | None = None

    # Interactive API docs (/docs, /redoc, /openapi.json): 404 unless enabled (dev only).
    docs_enabled: bool = False

    # Throttle for wrong or missing bearer tokens (core/throttle.py): after `auth_max_failures`
    # failures within `auth_failure_window_s` seconds from one client IP, requests from that IP
    # without a valid token get 429. A valid token is never throttled.
    auth_max_failures: int = 20
    auth_failure_window_s: int = 600
    # Comma-separated IPs / CIDRs of reverse proxies whose X-Forwarded-For header is believed
    # (the right-most entry is then the client IP); any other peer is used as is. The default is
    # the private ranges: in deploy/docker-compose.yml the backend is only reachable from Caddy,
    # whose container sits in a Docker bridge network (172.16.0.0/12 by default, any RFC 1918 range
    # when the daemon is configured otherwise). Caddy replaces a client-sent X-Forwarded-For with
    # the real address. Local dev (peer 127.0.0.1) is not trusted, so the header is ignored there.
    trusted_proxy_ips: str = "10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,fc00::/7"

    # Scheduler (APScheduler in the FastAPI process); off by default locally.
    scheduler_enabled: bool = False
    scheduler_tz: str = "Europe/Vienna"

    # Claude credentials: `anthropic_api_key` (Messages API, paid per token) and
    # `claude_code_oauth_token` (headless `claude -p` on a Claude Pro/Max subscription, from
    # `claude setup-token`). Which provider an app uses, and with which model, is the app's setting.
    anthropic_api_key: SecretStr | None = None
    claude_code_oauth_token: SecretStr | None = None


@lru_cache
def get_core_settings() -> CoreSettings:
    return CoreSettings()
