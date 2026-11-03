"""The Withings token file (`settings.withings_token_cache_path`, mode 0600, written atomically).

Withings refresh tokens ROTATE: every refresh returns a new refresh token and the old one stops
working, so the new pair must be on disk before it is used. A file (like the Garmin token cache)
rather than the database keeps secrets out of the DB snapshots and the app sync.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from core.redact import register_secret


@dataclass(frozen=True)
class Tokens:
    userid: str
    access_token: str = field(repr=False)
    refresh_token: str = field(repr=False)
    expires_at: float  # epoch seconds
    scope: str = ""

    def register(self) -> None:
        register_secret(self.access_token)
        register_secret(self.refresh_token)


class TokenStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def exists(self) -> bool:
        return self.load() is not None

    def load(self) -> Tokens | None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            tokens = Tokens(
                userid=str(data["userid"]),
                access_token=str(data["access_token"]),
                refresh_token=str(data["refresh_token"]),
                expires_at=float(data["expires_at"]),
                scope=str(data.get("scope", "")),
            )
        except (OSError, KeyError, TypeError, ValueError):
            return None
        if not tokens.refresh_token:
            return None
        tokens.register()
        return tokens

    def save(self, tokens: Tokens) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(f".{self.path.name}.tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        os.chmod(tmp, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(
                {
                    "userid": tokens.userid,
                    "access_token": tokens.access_token,
                    "refresh_token": tokens.refresh_token,
                    "expires_at": tokens.expires_at,
                    "scope": tokens.scope,
                },
                fh,
            )
            fh.flush()
            os.fsync(fh.fileno())
        tmp.replace(self.path)
        tokens.register()

    def delete(self) -> None:
        self.path.unlink(missing_ok=True)
