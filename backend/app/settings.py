"""Application settings, loaded from environment / `.env` via pydantic-settings.

Secrets (Garmin password, API keys) are `SecretStr` so they never end up in logs or reprs.
"""

from __future__ import annotations

from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_DIR.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(BACKEND_DIR / ".env", REPO_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = f"sqlite:///{BACKEND_DIR / 'var' / 'fitness.db'}"
    user_tz: str = "Europe/Vienna"
    default_gym_name: str = "Main gym"

    imports_dir: Path = REPO_ROOT / "imports"
    reports_dir: Path = REPO_ROOT / "imports" / "reports"
    catalog_path: Path = BACKEND_DIR / "data" / "catalog" / "exercises.yaml"
    strong_mapping_path: Path = BACKEND_DIR / "data" / "catalog" / "strong_mapping.csv"
    gyms_path: Path = BACKEND_DIR / "data" / "catalog" / "gyms.yaml"
    engine_config_path: Path = (
        BACKEND_DIR / "config" / "engine.v3.yaml"
    )  # SEED only, see config_service
    program_path: Path = BACKEND_DIR / "data" / "program" / "program.yaml"
    engine_auto_run: bool = True  # run the engine after a Garmin sync brought new data

    # Strong sessions longer than this are considered bad data: duration is stored as NULL.
    strong_max_duration_h: float = 4.0

    # Shared bearer token for the write endpoints (bodyweight). Unset => those endpoints refuse.
    api_token: SecretStr | None = None

    # Strong exports without unit columns: unit the app was set to when exporting.
    strong_default_weight_unit: str = "kg"
    strong_default_distance_unit: str = "km"

    # Garmin. Secrets are SecretStr so they never end up in logs or reprs.
    # personal_api = official Personal API (pull-only), garminconnect = python-garminconnect with
    # GARMIN_EMAIL/GARMIN_PASSWORD, auto = personal API first, fallback only if it fails / has gaps.
    garmin_source: Literal["personal_api", "garminconnect", "auto"] = "auto"
    garmin_personal_api_key: SecretStr | None = None
    garmin_personal_api_base_url: str = "https://personal.connectapi.garmin.com"
    garmin_email: str | None = None
    garmin_password: SecretStr | None = None
    garmin_request_interval_s: float = 1.0  # polite pacing between requests
    garmin_http_timeout_s: float = 30.0
    garmin_max_retries: int = 5  # per request, for 429 / 5xx / network errors
    garmin_token_cache_path: Path = BACKEND_DIR / "var" / "garmin_token.json"
    garmin_connect_token_dir: Path = BACKEND_DIR / "var" / "garminconnect"
    garmin_fit_dir: Path = BACKEND_DIR / "var" / "fit"
    garmin_since: date = date(2023, 1, 1)  # default backfill start
    garmin_sync_overlap_days: int = 3  # incremental sync re-reads this many days
    garmin_download_fit: bool = True  # store the original .FIT (needed for laps)
    # Planned runs -> Garmin Connect workouts on the watch (app/garmin_workouts.py, needs
    # GARMIN_EMAIL/GARMIN_PASSWORD; docs/api/garmin.md). Window = today + this many days ahead.
    garmin_push_workouts: bool = True
    garmin_push_days: int = 2
    garmin_push_times: str = "19:45,05:00"  # scheduler_tz; after the 19:30 / 03:30 syncs
    garmin_push_after_engine: bool = True  # also push when an engine re-run changed a run

    # Scheduler (APScheduler in the FastAPI process); off by default locally.
    scheduler_enabled: bool = False
    scheduler_tz: str = "Europe/Vienna"
    sync_nightly_time: str = "03:30"  # full incremental sync
    sync_day_times: str = "07:30,11:30,15:30,19:30"  # lighter runs (activities + today's health)

    # Claude advisor (app/advisor). No credential => consultations wait with status `awaiting_key`.
    # Two providers: `api` (Messages API, paid per token, ANTHROPIC_API_KEY) and `claude_code`
    # (headless `claude -p` billed to a Claude Pro/Max subscription through the long-lived
    # CLAUDE_CODE_OAUTH_TOKEN of `claude setup-token`). auto = claude_code if that token is set,
    # else api if a key is set, else awaiting_key.
    advisor_provider: Literal["api", "claude_code", "auto"] = "auto"
    anthropic_api_key: SecretStr | None = None
    claude_code_oauth_token: SecretStr | None = None
    advisor_cli_path: str = "claude"  # the Claude Code binary (looked up on PATH)
    advisor_cli_timeout_s: float = 300.0
    advisor_cli_effort: str = "high"  # `claude --effort`: low | medium | high | xhigh | max
    advisor_model: str = "claude-opus-5-5"
    advisor_dry_run: bool = False  # build + store the input, never call Claude
    advisor_max_tokens: int = 16000
    advisor_max_retries: int = 3  # SDK retries (429 / 5xx / connection) with exponential backoff
    advisor_timeout_s: float = 600.0
    advisor_weekly_time: str = "20:00"  # every Sunday, scheduler_tz
    advisor_trigger_time: str = "18:30"  # daily (not Sundays): batch open advisor_triggers
    advisor_outcome_time: str = "04:15"  # daily: outcomes of changes applied 21-28 days ago

    @property
    def advisor_api_key(self) -> str | None:
        """The Messages API key if one is configured (blank counts as unset)."""
        key = self.anthropic_api_key.get_secret_value().strip() if self.anthropic_api_key else ""
        return key or None

    @property
    def advisor_oauth_token(self) -> str | None:
        """The Claude Code subscription token if one is configured (blank counts as unset)."""
        tok = (
            self.claude_code_oauth_token.get_secret_value().strip()
            if self.claude_code_oauth_token
            else ""
        )
        return tok or None

    @property
    def advisor_provider_resolved(self) -> Literal["api", "claude_code", "none"]:
        """Which provider will run: the requested one, or (auto) the first with a credential."""
        if self.advisor_provider == "api":
            return "api" if self.advisor_api_key else "none"
        if self.advisor_provider == "claude_code":
            return "claude_code" if self.advisor_oauth_token else "none"
        if self.advisor_oauth_token:
            return "claude_code"
        return "api" if self.advisor_api_key else "none"

    @property
    def advisor_key(self) -> str | None:
        """The credential of the resolved provider, or None (=> `awaiting_key`).

        Callers only test it against None ("is the advisor usable?"); it is a secret.
        """
        provider = self.advisor_provider_resolved
        if provider == "claude_code":
            return self.advisor_oauth_token
        if provider == "api":
            return self.advisor_api_key
        return None


@lru_cache
def get_settings() -> Settings:
    return Settings()
