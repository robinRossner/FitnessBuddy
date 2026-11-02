"""Fitness settings: the shared `core.settings.CoreSettings` (API token, scheduler switches,
Claude credentials) plus everything fitness-specific, loaded from environment / `.env`.

Secrets (Garmin password, API keys) are `SecretStr` so they never end up in logs or reprs.
"""

from __future__ import annotations

from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import SecretStr

from core.settings import BACKEND_DIR, REPO_ROOT, CoreSettings

FITNESS_DIR = Path(__file__).resolve().parent  # backend/apps/fitness: data, config, alembic
# BACKEND_DIR (var/, .env) and REPO_ROOT (imports/) come from core.settings.


class Settings(CoreSettings):
    database_url: str = f"sqlite:///{BACKEND_DIR / 'var' / 'fitness.db'}"
    user_tz: str = "Europe/Vienna"
    default_gym_name: str = "Main gym"

    imports_dir: Path = REPO_ROOT / "imports"
    reports_dir: Path = REPO_ROOT / "imports" / "reports"
    catalog_path: Path = FITNESS_DIR / "data" / "catalog" / "exercises.yaml"
    strong_mapping_path: Path = FITNESS_DIR / "data" / "catalog" / "strong_mapping.csv"
    gyms_path: Path = FITNESS_DIR / "data" / "catalog" / "gyms.yaml"
    engine_config_path: Path = (
        FITNESS_DIR / "config" / "engine.v3.yaml"
    )  # SEED only, see config_service
    # SEED only: the live program is `live_program_path` (next to the database, see below)
    program_path: Path = FITNESS_DIR / "data" / "program" / "program.yaml"
    # The live program file (the engine reads it, the app's "Update template" and `engine program
    # generate` write it). Default: `program.yaml` in the database's directory (the persistent
    # volume: backend/var locally, /app/var = deploy/data on the server).
    program_live_path: Path | None = None
    engine_auto_run: bool = True  # run the engine after a Garmin sync brought new data

    # Strong sessions longer than this are considered bad data: duration is stored as NULL.
    strong_max_duration_h: float = 4.0

    # api_token (bearer auth), scheduler_enabled / scheduler_tz and the Claude credentials
    # (anthropic_api_key, claude_code_oauth_token) are inherited from core.settings.CoreSettings.

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
    # Planned runs -> Garmin Connect workouts on the watch (apps/fitness/garmin/workouts.py, needs
    # GARMIN_EMAIL/GARMIN_PASSWORD; docs/api/garmin.md). Window = today + this many days ahead.
    garmin_push_workouts: bool = True
    garmin_push_days: int = 2
    # scheduler_tz; after the 19:30 / 03:30 syncs (later changes go via garmin_push_after_engine)
    garmin_push_times: str = "19:45,05:00"
    # Manual sync (POST /garmin/sync, in-memory guard, restart resets it): minimum gap between two
    # attempts and attempts per local day. Garmin publishes no Personal API limit; stay far below.
    garmin_manual_sync_cooldown_s: int = 30
    garmin_manual_sync_daily_max: int = 30
    garmin_push_after_engine: bool = True  # also push when an engine re-run changed a run

    # Scheduler job times (the switch itself is core's `scheduler_enabled`).
    sync_nightly_time: str = "03:30"  # full incremental sync
    # lighter runs (activities, daily summary, sleep, HRV). The early ones catch the morning: sleep
    # and HRV can land after waking and not at the same time.
    sync_day_times: str = "07:30,08:30,09:30,11:30,15:30,19:30"

    # Claude advisor (apps/fitness/advisor). No credential => consultations wait as `awaiting_key`.
    # Two providers: `api` (Messages API, paid per token, ANTHROPIC_API_KEY) and `claude_code`
    # (headless `claude -p` billed to a Claude Pro/Max subscription through the long-lived
    # CLAUDE_CODE_OAUTH_TOKEN of `claude setup-token`). auto = claude_code if that token is set,
    # else api if a key is set, else awaiting_key.
    advisor_provider: Literal["api", "claude_code", "auto"] = "auto"
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
    def live_program_path(self) -> Path:
        if self.program_live_path is not None:
            return self.program_live_path
        from sqlalchemy.engine import make_url

        db = make_url(self.database_url).database
        base = Path(db).parent if db and db != ":memory:" else BACKEND_DIR / "var"
        return base / "program.yaml"

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
