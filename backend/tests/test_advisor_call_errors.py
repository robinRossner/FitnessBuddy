"""How a failed provider call (AdvisorCallError.kind) maps to a consultation status, the cost of a
subscription run, and the provider block of GET /coach/settings."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from apps.fitness.advisor import claude, service
from apps.fitness.advisor.providers.base import AdvisorCallError, ClaudeResult
from apps.fitness.db.models import AdvisorTrigger
from tests.advisor_helpers import make_output
from tests.test_advisor_service import consult


def raising(kind: str, message: str = "boom"):
    def _consult(settings, user_text, *, client=None):
        raise AdvisorCallError(kind, message)

    return _consult


def test_auth_error_waits_as_awaiting_key_and_keeps_the_message(db, world, no_rerun, monkeypatch):
    monkeypatch.setattr(claude, "consult", raising("auth", "Login expired: run claude setup-token"))
    c = consult(db)
    assert c.status == "awaiting_key" and c.error == "Login expired: run claude setup-token"
    assert not c.changes


def test_auth_error_is_retried_once_the_login_works(db, world, no_rerun, monkeypatch, fake):
    real = claude.consult
    monkeypatch.setattr(claude, "consult", raising("auth"))
    c = consult(db)
    assert c.status == "awaiting_key"
    monkeypatch.setattr(claude, "consult", real)  # the login is fixed
    assert service.retry_awaiting_key(db) == 1
    db.refresh(c)
    assert c.status == "pending"


def test_usage_limit_fails_with_a_fixed_message_and_triggers_stay_for_the_next_run(
    db, world, no_rerun, monkeypatch
):
    monkeypatch.setattr(claude, "consult", raising("usage_limit", "5-hour limit reached"))
    db.add(
        AdvisorTrigger(
            type="lift_stall", dedupe_key="ls:1", subject_id="1", payload={"variant": "Bench"}
        )
    )
    db.commit()
    c = service.run_trigger_batch(db)
    assert c.status == "failed" and c.error == "Subscription usage limit reached, retry later"
    trig = db.scalars(select(AdvisorTrigger)).all()
    assert [t.status for t in trig] == ["open"]  # the next daily batch picks them up again


def test_usage_limited_ask_is_requeued_by_the_daily_job(db, world, no_rerun, monkeypatch, fake):
    real = claude.consult
    monkeypatch.setattr(claude, "consult", raising("usage_limit"))
    c = service.start_ask(db, scope="general", ref_id=None, question="How am I doing?")
    service.execute(db, c.id)
    assert c.status == "failed" and c.usage["requeue"] is True
    monkeypatch.setattr(claude, "consult", real)
    assert service.retry_usage_limited(db) == 1
    db.refresh(c)
    assert c.status == "pending" and c.error is None and not (c.usage or {}).get("requeue")
    assert service.retry_usage_limited(db) == 0  # only once


@pytest.mark.parametrize("kind", ["timeout", "cli_missing", "cli_error", "invalid_output"])
def test_every_other_kind_is_a_normal_failure(db, world, no_rerun, monkeypatch, kind):
    monkeypatch.setattr(claude, "consult", raising(kind, f"{kind} happened"))
    c = consult(db)
    assert c.status == "failed" and c.error == f"{kind} happened"
    assert not (c.usage or {}).get("requeue")


def test_subscription_run_stores_no_cost(db, world, no_rerun, monkeypatch):
    usage = {
        "input_tokens": 5000,
        "output_tokens": 900,
        "cache_read_input_tokens": 0,
        "cache_creation_input_tokens": 0,
        "billing": "subscription",
    }
    monkeypatch.setattr(
        claude,
        "consult",
        lambda settings, text, *, client=None: ClaudeResult(
            "ok", make_output(), "end_turn", usage=usage
        ),
    )
    c = consult(db)
    assert c.cost_usd is None and c.tokens_in == 5000 and c.tokens_out == 900


def test_api_run_still_computes_the_cost(db, world, fake, no_rerun):
    assert consult(db).cost_usd and consult(db).cost_usd > 0


def test_settings_carry_the_provider_status(client):
    s = client.get("/api/v1/coach/settings").json()
    assert s["provider"] == "api" and s["requested_provider"] == "auto"
    assert s["credential_configured"] is True and s["billing"] == "api"
    assert s["model"] and s["api_key_configured"] is True and s["approval_mode"] == "manual"
