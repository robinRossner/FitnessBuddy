"""Application settings, loaded from environment / `.env` via pydantic-settings.

Secrets (Garmin password, API keys) are `SecretStr` so they never end up in logs or reprs.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

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
    engine_config_path: Path = BACKEND_DIR / "config" / "engine.v2.yaml"

    # Strong sessions longer than this are considered bad data: duration is stored as NULL.
    strong_max_duration_h: float = 4.0

    # Shared bearer token for the write endpoints (bodyweight). Unset => those endpoints refuse.
    api_token: SecretStr | None = None

    # Strong exports without unit columns: unit the app was set to when exporting.
    strong_default_weight_unit: str = "kg"
    strong_default_distance_unit: str = "km"

    # Later steps (not used yet). SecretStr keeps them out of logs.
    garmin_email: str | None = None
    garmin_password: SecretStr | None = None
    anthropic_api_key: SecretStr | None = None


@lru_cache
def get_settings() -> Settings:
    return Settings()
