"""Withings endpoints (docs/api/sync.md "Withings"): status, connect, sync, disconnect behind the
bearer token, plus the public OAuth callback Withings redirects the user's browser to."""

from __future__ import annotations

from datetime import datetime
from html import escape
from typing import Annotated, Any, Literal
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Query, Response, status
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from apps.fitness.api.deps import get_db, get_settings_dep, service_errors
from apps.fitness.settings import Settings
from apps.fitness.withings import service
from core.auth import require_token

router = APIRouter(prefix="/withings", dependencies=[Depends(require_token)])
public_router = APIRouter(prefix="/withings")


class StatusOut(BaseModel):
    configured: bool
    connected: bool
    last_sync_at: datetime | None
    last_error: str | None
    latest_measured_at: datetime | None
    latest_weight_kg: float | None


class ConnectIn(BaseModel):
    client: Literal["app", "web"] = "app"


class ConnectOut(BaseModel):
    authorize_url: str


class SyncOut(BaseModel):
    status: Literal["synced", "busy", "not_connected"]
    new_measurements: int
    latest_measured_at: datetime | None
    latest_weight_kg: float | None


@router.get("/status", response_model=StatusOut)
def get_status(
    db: Session = Depends(get_db), settings: Settings = Depends(get_settings_dep)
) -> dict[str, Any]:
    return service.status(db, settings)


@router.post("/connect", response_model=ConnectOut)
def connect(
    body: ConnectIn | None = None, settings: Settings = Depends(get_settings_dep)
) -> dict[str, str]:
    with service_errors():
        return {"authorize_url": service.authorize_url((body or ConnectIn()).client, settings)}


@router.post("/sync", response_model=SyncOut)
def sync(
    db: Session = Depends(get_db), settings: Settings = Depends(get_settings_dep)
) -> dict[str, Any]:
    with service_errors():
        return service.sync_now(db, settings)


@router.delete("/connection", status_code=status.HTTP_204_NO_CONTENT)
def disconnect(settings: Settings = Depends(get_settings_dep)) -> Response:
    service.disconnect(settings)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --------------------------------------------------------------------------- OAuth callback

_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>{title}</title>
<style>body{{font:16px -apple-system,system-ui,sans-serif;max-width:30rem;margin:4rem auto;\
padding:0 1rem;text-align:center}}</style></head>
<body><h1>{title}</h1><p>{text}</p></body></html>"""

_FAILED = {
    "denied": "Access was not granted. You can close this page and try again.",
    "invalid_state": "This link is invalid or has expired. Start the connection again.",
    "exchange_failed": "Withings did not accept the connection. Start the connection again.",
    "not_configured": "Withings is not configured on this server.",
}


def _html(result: service.CallbackResult) -> HTMLResponse:
    if result.ok:
        title, text, code = "Withings connected", "You can close this page.", 200
    else:
        reason = result.reason or "exchange_failed"
        title, text = "Withings connection failed", _FAILED.get(reason, _FAILED["exchange_failed"])
        code = 503 if reason == "not_configured" else 400
    return HTMLResponse(
        _PAGE.format(title=escape(title), text=escape(text)),
        status_code=code,
        headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"},
    )


@public_router.head("/callback")
def callback_probe() -> Response:
    """The developer dashboard checks a registered URL answers 200 before accepting it."""
    return Response(status_code=status.HTTP_200_OK)


@public_router.get("/callback")
def callback(
    code: Annotated[str | None, Query(max_length=2000)] = None,
    state: Annotated[str | None, Query(max_length=500)] = None,
    error: Annotated[str | None, Query(max_length=200)] = None,
    settings: Settings = Depends(get_settings_dep),
) -> Response:
    if code is None and state is None and error is None:
        # A bare GET is the dashboard's URL check (or someone opening the link), not a redirect.
        return HTMLResponse(
            _PAGE.format(title="Withings callback", text="Nothing to do here."),
            headers={"Cache-Control": "no-store"},
        )
    result = service.handle_callback(code, state, error, settings)
    if result.client != "app" or result.reason in ("invalid_state", "not_configured"):
        return _html(result)
    query = {"result": "connected"} if result.ok else {"result": "error", "reason": result.reason}
    base = settings.withings_app_redirect_url
    sep = "&" if "?" in base else "?"
    return RedirectResponse(
        f"{base}{sep}{urlencode(query)}", status_code=302, headers={"Cache-Control": "no-store"}
    )
