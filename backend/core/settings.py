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

    # Shared bearer token for the write endpoints. Unset => those endpoints refuse.
    api_token: SecretStr | None = None

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
