"""Shared-secret bearer auth for every endpoint except the public health checks."""

from __future__ import annotations

import secrets
from typing import Annotated

from fastapi import Depends, Header, HTTPException

from app.settings import get_settings


def get_api_token() -> str | None:
    token = get_settings().api_token
    return token.get_secret_value() if token else None


def require_token(
    authorization: Annotated[str | None, Header()] = None,
    token: str | None = Depends(get_api_token),
) -> None:
    if not token:
        raise HTTPException(503, "API_TOKEN is not configured on the server")
    scheme, _, supplied = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not secrets.compare_digest(supplied.strip(), token):
        raise HTTPException(401, "invalid or missing bearer token", {"WWW-Authenticate": "Bearer"})
