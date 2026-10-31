"""Build the configured GarminSource from settings."""

from __future__ import annotations

from apps.fitness.settings import Settings
from apps.fitness.sources.garmin.base import GarminSource
from apps.fitness.sources.garmin.composite import CompositeSource
from apps.fitness.sources.garmin.errors import SourceUnavailable
from apps.fitness.sources.garmin.personal_api import PersonalApiSource
from apps.fitness.sources.garmin.personal_client import PersonalApiClient
from apps.fitness.sources.garmin.python_garminconnect import PythonGarminConnectSource


def _personal(s: Settings) -> PersonalApiSource:
    key = s.garmin_personal_api_key
    if key is None or not key.get_secret_value().strip():
        raise SourceUnavailable("GARMIN_PERSONAL_API_KEY is not set (see backend/.env.example)")
    return PersonalApiSource(
        PersonalApiClient(
            key.get_secret_value(),
            base_url=s.garmin_personal_api_base_url,
            token_cache_path=s.garmin_token_cache_path,
            interval_s=s.garmin_request_interval_s,
            timeout_s=s.garmin_http_timeout_s,
            max_retries=s.garmin_max_retries,
        )
    )


def _connect(s: Settings) -> PythonGarminConnectSource | None:
    if not (s.garmin_email and s.garmin_password):
        return None
    return PythonGarminConnectSource(
        s.garmin_email,
        s.garmin_password.get_secret_value(),
        s.garmin_connect_token_dir,
        pace_s=s.garmin_request_interval_s,
    )


def build_source(s: Settings, override: str | None = None) -> GarminSource:
    mode = override or s.garmin_source
    if mode == "personal_api":
        return _personal(s)
    if mode == "garminconnect":
        src = _connect(s)
        if src is None:
            raise SourceUnavailable("GARMIN_EMAIL / GARMIN_PASSWORD are not set")
        return src
    # auto: Personal API (if a key exists) + optional fallback
    fallback = _connect(s)
    has_key = s.garmin_personal_api_key is not None
    if not has_key:
        if fallback is None:
            raise SourceUnavailable("neither GARMIN_PERSONAL_API_KEY nor GARMIN_EMAIL is set")
        return fallback
    return CompositeSource(_personal(s), fallback)
