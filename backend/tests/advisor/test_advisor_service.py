"""Advisor lifecycle with a mocked Claude client: parsing, refusal / cut-off / API errors, missing
key, dry run, validation of each change, approval modes, approve / reject, budget guards, the
trigger batch, the weekly review and the input size."""

from __future__ import annotations

import json

import anthropic
import httpx2
import pytest
from sqlalchemy import select

from apps.fitness.advisor import claude, service, summary
from apps.fitness.advisor.prompt import SYSTEM_PROMPT
from apps.fitness.db.models import AdvisorChange, AdvisorConsultation, AdvisorTrigger
from apps.fitness.engine.store import config_service as cs
from apps.fitness.settings import get_settings
from tests.support.fakes_anthropic import KEY, FakeClient, make_output
from tests.support.world import TODAY, configure


def consult(db, **kw):
    """Create + execute one ask-less 'trigger' style consultation."""
    c = service.create_consultation(
        db,
        kind=kw.pop("kind", "trigger"),
        trigger_info=kw.pop(
            "info",
            {
                "type": "lift_stall",
                "title": "Stalled lift: Bench",
                "detail": "flat",
                "ref": {"kind": "lift", "id": "1"},
            },
        ),
    )
    db.commit()
    return service.execute(db, c.id, **kw)


def changes(c):
    return {x.param: x for x in c.changes}


# --------------------------------------------------------------------------- request + parsing


def test_request_shape(db, world, fake, no_rerun):
    c = consult(db)
    assert c.status == "pending"
    (call,) = fake.calls
    assert call["model"] == "claude-opus-5-5"
    assert call["thinking"] == {"type": "adaptive"}
    assert call["output_config"] == {"effort": "high"}
    assert call["max_tokens"] == 16000
    assert call["fallbacks"] == "default"
    assert call["betas"] == ["server-side-fallback-2026-07-01"]
    assert call["output_format"].__name__ == "AdvisorOutput"
    assert "tool_choice" not in call and "stream" not in call
    # frozen system prompt with the cache breakpoint; nothing volatile in it
    (block,) = call["system"]
    assert block["text"] == SYSTEM_PROMPT and block["cache_control"] == {"type": "ephemeral"}
    assert "2026" not in SYSTEM_PROMPT
    # all data is in the user message, after the cached system prompt
    (msg,) = call["messages"]
    assert msg["role"] == "user" and "<input>" in msg["content"] and "cache_control" not in str(msg)
    assert "assistant" not in [m["role"] for m in call["messages"]]  # no prefill


def test_valid_answer_is_stored_and_each_change_validated(db, world, fake, no_rerun):
    c = consult(db)
    assert c.status == "pending" and c.model == "claude-opus-5-5"
    ch = changes(c)
    rep = ch["exercise_overrides.bench_press.rep_range"]
    assert rep.valid and rep.status == "proposed" and rep.risk == "low"
    assert rep.old_value == [7, 12] and rep.new_value == [8, 12]
    assert rep.label == "Bench Press (Barbell) rep range"
    cap = ch["running.weekly_volume_cap_pct"]
    assert cap.valid and cap.risk == "high" and cap.claude_risk == "medium"  # validator decides
    bad = ch["running.vdot.seed_race.time_s"]  # a pace-ish number: not on the allowlist
    assert not bad.valid and bad.status == "invalid" and "allowlist" in bad.validation_error
    assert c.response["diagnosis"].startswith("Bench press")
    assert c.response["proposed_changes"][0]["from"] == [7, 12]
    assert len(c.response["session_overrides"]) == 1


def test_usage_cost_and_log(db, world, fake, no_rerun, logs):
    caplog = logs
    c = consult(db)
    assert c.tokens_in == 8200 and c.tokens_out == 2500  # input + cache write
    assert c.usage["cache_creation_input_tokens"] == 2200 and c.usage["request_id"] == "req_test_1"
    # 6000 * 4 + 2500 * 20 + 2200 * 5 per million = 0.024 + 0.05 + 0.011
    assert c.cost_usd == pytest.approx(0.085, abs=1e-6)
    text = " ".join(r.getMessage() for r in caplog.records)
    assert "in=6000" in text and "out=2500" in text and "cache read 0, write 2200" in text
    assert "cost=$0.0850" in text


def test_cache_read_is_priced_at_the_read_rate(db, world, no_rerun, monkeypatch):
    client = FakeClient(
        usage={
            "input_tokens": 1000,
            "output_tokens": 0,
            "cache_read_input_tokens": 5_000_000,
            "cache_creation_input_tokens": 0,
        }
    )
    monkeypatch.setattr(claude, "make_client", lambda s: client)
    assert consult(db).cost_usd == pytest.approx(1000 * 4 / 1e6 + 5_000_000 * 0.2 / 1e6)


def test_session_overrides_with_weights_or_paces_are_dropped(db, world, fake, no_rerun):
    from apps.fitness.advisor.schema import SessionOverride

    fake.output = make_output(
        proposed_changes=[],
        session_overrides=[
            SessionOverride(
                date="2026-10-26", session="Upper", change="Bench 82.5 kg x 5", reason="x"
            ),
            SessionOverride(
                date="2026-10-27", session="Intervals", change="run 4:10/km", reason="x"
            ),
            SessionOverride(date="tomorrow", session="Upper", change="easy day", reason="x"),
            SessionOverride(
                date="2026-10-28", session="Easy", change="Keep it conversational.", reason="x"
            ),
        ],
    )
    c = consult(db)
    assert [o["session"] for o in c.response["session_overrides"]] == ["Easy"]
    assert len(c.response["rejected_session_overrides"]) == 3


def test_duplicate_and_excess_changes_are_invalid(db, world, fake, no_rerun):
    from apps.fitness.advisor.schema import ProposedChange

    one = ProposedChange(
        param="strength.stall_exposures", **{"from": 3}, to=4, reason="r", risk="low"
    )
    fake.output = make_output(proposed_changes=[one, one])
    c = consult(db)
    assert [x.valid for x in c.changes] == [True, False]
    assert "twice" in c.changes[1].validation_error


def test_answer_without_changes_is_closed_not_pending(db, world, fake, no_rerun):
    fake.output = make_output(proposed_changes=[], session_overrides=[])
    c = consult(db)
    assert c.status == "applied" and not c.changes and c.error is None


def test_all_invalid_changes_end_rejected(db, world, fake, no_rerun):
    from apps.fitness.advisor.schema import ProposedChange

    bad = ProposedChange(
        param="running.heart_rate.lthr", **{"from": 171}, to=180, reason="r", risk="low"
    )
    fake.output = make_output(proposed_changes=[bad])
    c = consult(db)
    assert c.status == "rejected" and c.changes[0].status == "invalid"


def test_invalid_changes_are_logged(db, world, fake, no_rerun, logs):
    caplog = logs
    consult(db)
    assert any("running.vdot.seed_race.time_s" in r.getMessage() for r in caplog.records)


# --------------------------------------------------------------------------- stop reasons + errors


def test_refusal_is_a_failed_consultation_without_changes(db, world, no_rerun, monkeypatch):
    client = FakeClient(
        stop_reason="refusal",
        stop_details=type("D", (), {"category": "cyber", "explanation": "x"})(),
    )
    monkeypatch.setattr(claude, "make_client", lambda s: client)
    c = consult(db)
    assert c.status == "failed" and "declined" in c.error and "cyber" in c.error
    assert not c.changes and c.response is None
    assert c.cost_usd is not None  # billed tokens are still recorded


def test_max_tokens_is_a_failed_consultation(db, world, no_rerun, monkeypatch):
    monkeypatch.setattr(claude, "make_client", lambda s: FakeClient(stop_reason="max_tokens"))
    c = consult(db)
    assert c.status == "failed" and "cut off" in c.error and "16000" in c.error
    assert not c.changes


def test_unknown_stop_reason_and_empty_output(db, world, no_rerun, monkeypatch):
    for client, text in ((FakeClient(stop_reason="pause_turn"), "stop reason"),):
        monkeypatch.setattr(claude, "make_client", lambda s, c=client: c)
        c = consult(db)
        assert c.status == "failed" and text in c.error


def _http_error(cls, status, message="boom"):
    req = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    resp = httpx2.Response(status, request=req, headers={"retry-after": "7"})
    return cls(message, response=resp, body=None)


def test_error_chain_rate_limit_status_connection(db, world, no_rerun, monkeypatch):
    req = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    cases = [
        (_http_error(anthropic.RateLimitError, 429), "rate limit"),
        (_http_error(anthropic.InternalServerError, 500, "overloaded"), "HTTP 500"),
        (_http_error(anthropic.AuthenticationError, 401, "invalid x-api-key"), "HTTP 401"),
        (anthropic.APIConnectionError(request=req), "Could not reach"),
        (anthropic.APITimeoutError(request=req), "Could not reach"),
    ]
    for exc, expected in cases:
        monkeypatch.setattr(claude, "make_client", lambda s, e=exc: FakeClient(exc=e))
        c = consult(db)
        assert c.status == "failed", expected
        assert expected.lower() in c.error.lower()
        assert c.changes == []


def test_error_kinds_come_from_the_specific_exception(env):
    for exc, kind in (
        (_http_error(anthropic.RateLimitError, 429), "rate_limit"),
        (_http_error(anthropic.BadRequestError, 400), "api_status"),
        (anthropic.APIConnectionError(request=httpx2.Request("POST", "https://x")), "connection"),
    ):
        with pytest.raises(claude.AdvisorCallError) as info:
            claude.consult(env, "hi", client=FakeClient(exc=exc))
        assert info.value.kind == kind


def test_error_text_never_contains_the_key(db, world, no_rerun, monkeypatch):
    exc = _http_error(anthropic.AuthenticationError, 401, f"bad key {KEY}")
    monkeypatch.setattr(claude, "make_client", lambda s: FakeClient(exc=exc))
    c = consult(db)
    assert KEY not in c.error and "sk-ant-" not in c.error


def test_crash_in_the_client_does_not_leave_the_row_running(db, world, no_rerun, monkeypatch):
    monkeypatch.setattr(claude, "make_client", lambda s: FakeClient(exc=RuntimeError("bug")))
    c = consult(db)
    assert c.status == "failed"


# --------------------------------------------------------------------------- key / dry run


def test_missing_key_gives_awaiting_key_and_nothing_crashes(db, monkeypatch, tmp_path, no_rerun):
    from tests.support.world import build_world

    configure(monkeypatch, tmp_path, key=None)
    build_world(db, monkeypatch)
    assert get_settings().advisor_key is None

    def explode(settings):  # must never be reached
        raise AssertionError("no client without a key")

    monkeypatch.setattr(claude, "make_client", explode)
    c = consult(db)
    assert c.status == "awaiting_key" and c.error is None
    assert c.request["why"]["type"] == "lift_stall"  # the input is still stored
    assert not c.changes and c.cost_usd is None


def test_blank_key_counts_as_missing(monkeypatch, tmp_path):
    s = configure(monkeypatch, tmp_path, key="   ")
    assert s.advisor_key is None


def test_make_client_without_key_raises_a_clear_error(monkeypatch, tmp_path):
    s = configure(monkeypatch, tmp_path, key=None)
    with pytest.raises(claude.AdvisorCallError) as info:
        claude.make_client(s)
    assert info.value.kind == "no_key"


def test_dry_run_builds_and_stores_the_input_without_calling(
    db, monkeypatch, tmp_path, fake, no_rerun
):
    from tests.support.world import build_world

    configure(monkeypatch, tmp_path, advisor_dry_run="true")
    build_world(db, monkeypatch)
    c = consult(db)
    assert c.status == "dry_run" and c.dry_run is True
    assert fake.calls == []
    assert c.request["lifts"] and c.request["config"]["version"]
    assert not c.changes and c.cost_usd is None


def test_awaiting_key_consultations_run_once_a_key_exists(db, monkeypatch, tmp_path, no_rerun):
    from tests.support.world import build_world

    configure(monkeypatch, tmp_path, key=None)
    build_world(db, monkeypatch)
    c = consult(db)
    assert c.status == "awaiting_key"
    assert service.retry_awaiting_key(db) == 0  # still no key
    configure(monkeypatch, tmp_path, key=KEY)
    client = FakeClient()
    monkeypatch.setattr(claude, "make_client", lambda s: client)
    assert service.retry_awaiting_key(db) == 1
    db.refresh(c)
    assert c.status == "pending" and len(client.calls) == 1


def test_executing_twice_calls_claude_once(db, world, fake, no_rerun):
    c = consult(db)
    service.execute(db, c.id)
    assert len(fake.calls) == 1


# --------------------------------------------------------------------------- approval modes


def _set_mode(db, mode):
    cs.create_version(db, {"advisor.approval_mode": mode}, reason="test", author="user")
    db.commit()


def test_manual_mode_applies_nothing(db, world, fake, no_rerun):
    before = cs.get_active(db).version
    c = consult(db)
    assert c.status == "pending" and cs.get_active(db).version == before and no_rerun == []


def test_auto_low_risk_applies_only_what_the_validator_calls_low(db, world, fake, no_rerun):
    _set_mode(db, "auto-low-risk")
    c = consult(db)
    ch = changes(c)
    assert ch["exercise_overrides.bench_press.rep_range"].status == "applied"
    assert ch["running.weekly_volume_cap_pct"].status == "proposed"  # high risk: waits
    assert c.status == "partially_applied"
    active = cs.get_active(db)
    assert active.author == "advisor" and active.meta["consultation_id"] == c.id
    assert active.content["exercise_overrides"]["bench_press"]["rep_range"] == [8, 12]
    assert active.content["running"]["weekly_volume_cap_pct"] == 10
    assert no_rerun == ["rerun"]  # the engine is re-run after applying


def test_auto_low_risk_ignores_the_risk_claude_states(db, world, fake, no_rerun):
    from apps.fitness.advisor.schema import ProposedChange

    _set_mode(db, "auto-low-risk")
    sneaky = ProposedChange(  # Claude says low, the validator says medium
        param="strength.stall_exposures", **{"from": 3}, to=4, reason="r", risk="low"
    )
    fake.output = make_output(proposed_changes=[sneaky])
    c = consult(db)
    assert c.changes[0].risk == "medium" and c.changes[0].status == "proposed"
    assert c.status == "pending" and no_rerun == []


def test_auto_mode_applies_every_valid_change_in_one_version(db, world, fake, no_rerun):
    _set_mode(db, "auto")
    versions_before = len(cs.history(db))
    c = consult(db)
    assert c.status == "applied"  # the invalid change is not a decision
    ch = changes(c)
    assert ch["running.weekly_volume_cap_pct"].status == "applied"
    assert ch["running.vdot.seed_race.time_s"].status == "invalid"
    assert len(cs.history(db)) == versions_before + 1
    active = cs.get_active(db)
    assert active.author == "advisor" and "consultation 1" in active.reason
    assert set(active.diff) == {
        "exercise_overrides.bench_press.rep_range",
        "running.weekly_volume_cap_pct",
    }
    assert ch["running.weekly_volume_cap_pct"].applied_config_version_id == active.id
    assert c.applied_config_version_id == active.id


# --------------------------------------------------------------------------- approve / reject


def test_approve_subset_makes_a_config_version_authored_by_advisor(db, world, fake, no_rerun):
    c = consult(db)
    rep = changes(c)["exercise_overrides.bench_press.rep_range"]
    version = service.approve(db, c, [str(rep.id)])
    assert version.author == "advisor" and version.meta["consultation_id"] == c.id
    assert version.reason.startswith("Advisor (consultation") and "Flat e1RM" in version.reason
    assert version.diff == {
        "exercise_overrides.bench_press.rep_range": {"old": None, "new": [8, 12]}
    }
    db.refresh(c)
    assert rep.status == "applied" and rep.applied_config_version_id == version.id
    assert c.status == "partially_applied"  # the cap change is still proposed
    assert changes(c)["running.weekly_volume_cap_pct"].status == "proposed"
    # the rest can be approved later, as its own version
    v2 = service.approve(db, c, None)
    assert v2.id != version.id and c.status == "applied"
    assert cs.get_active(db).content["running"]["weekly_volume_cap_pct"] == 12


def test_approve_all_valid_when_ids_are_null(db, world, fake, no_rerun):
    c = consult(db)
    version = service.approve(db, c, None)
    assert version.author == "advisor"
    assert {x.status for x in c.changes} == {"applied", "invalid"}
    assert c.status == "applied"


def test_approve_is_idempotent(db, world, fake, no_rerun):
    c = consult(db)
    first = service.approve(db, c, None)
    count = len(cs.history(db))
    assert service.approve(db, c, None) is None
    assert service.approve(db, c, [str(c.changes[0].id)]) is None
    assert len(cs.history(db)) == count and cs.get_active(db).id == first.id


def test_approve_rejects_foreign_invalid_and_rejected_changes(db, world, fake, no_rerun):
    c = consult(db)
    bad = changes(c)["running.vdot.seed_race.time_s"]
    with pytest.raises(service.Unprocessable):
        service.approve(db, c, [str(bad.id)])
    with pytest.raises(service.Unprocessable):
        service.approve(db, c, ["99999"])
    service.reject(db, c, "no thanks")
    with pytest.raises(service.Conflict):
        service.approve(db, c, None)


def test_approve_is_conflict_when_nothing_ran(db, monkeypatch, tmp_path, no_rerun):
    from tests.support.world import build_world

    configure(monkeypatch, tmp_path, key=None)
    build_world(db, monkeypatch)
    c = consult(db)
    with pytest.raises(service.Conflict):
        service.approve(db, c, None)


def test_change_that_became_invalid_is_not_applied(db, world, fake, no_rerun):
    c = consult(db)
    cap = changes(c)["running.weekly_volume_cap_pct"]
    cs.create_version(
        db, {"running.weekly_volume_cap_pct": 12}, reason="user did it", author="user"
    )
    db.commit()
    version = service.approve(db, c, [str(cap.id)])
    assert version is None
    assert cap.status == "invalid" and "no longer valid" in cap.validation_error


def test_reject_marks_proposed_changes_and_is_idempotent(db, world, fake, no_rerun):
    c = consult(db)
    before = cs.get_active(db).id
    service.reject(db, c, "not now")
    assert c.status == "rejected" and c.reject_note == "not now"
    assert {x.status for x in c.changes} == {"rejected", "invalid"}
    service.reject(db, c, "again")
    assert c.reject_note == "not now" and cs.get_active(db).id == before


def test_reject_after_partial_approval_keeps_the_applied_part(db, world, fake, no_rerun):
    c = consult(db)
    rep = changes(c)["exercise_overrides.bench_press.rep_range"]
    service.approve(db, c, [str(rep.id)])
    service.reject(db, c, None)
    assert c.status == "partially_applied" and rep.status == "applied"
    assert changes(c)["running.weekly_volume_cap_pct"].status == "rejected"
    assert service.pending_proposals(db) == []


def test_applied_overrides_reach_the_engine_inputs(db, world, fake, no_rerun, env):
    from apps.fitness.engine.store import data as D
    from apps.fitness.engine.store import service as engine_service

    c = consult(db)
    service.approve(db, c, None)
    cfg, row = cs.load_active(db)
    inputs = D.load_inputs(
        db,
        today=TODAY,
        week_start=TODAY,
        cfg=cfg,
        config_version=row.version,
        program=engine_service.get_program(env),
        gyms_path=env.gyms_path,
    )
    bench = next(pe for pe in inputs.program.exercises("upper") if pe.exercise == "bench_press")
    assert bench.rep_range == (8, 12)


def test_swap_override_changes_the_program_variant(db, world, fake, no_rerun, env):
    from apps.fitness.advisor.schema import ProposedChange
    from apps.fitness.engine.store import data as D
    from apps.fitness.engine.store import service as engine_service

    fake.output = make_output(
        proposed_changes=[
            ProposedChange(
                param="exercise_overrides.incline_bench_press.variant",
                **{"from": "Incline Bench Press (Smith Machine)"},
                to="Incline Bench Press (Dumbbell)",
                reason="Smith machine feels off.",
                risk="medium",
            )
        ]
    )
    c = consult(db)
    assert c.changes[0].valid
    service.approve(db, c, None)
    cfg, row = cs.load_active(db)
    inputs = D.load_inputs(
        db,
        today=TODAY,
        week_start=TODAY,
        cfg=cfg,
        config_version=row.version,
        program=engine_service.get_program(env),
        gyms_path=env.gyms_path,
    )
    names = [pe.variant for pe in inputs.program.exercises("upper")]
    assert "Incline Bench Press (Dumbbell)" in names
    assert "Incline Bench Press (Smith Machine)" not in names


# --------------------------------------------------------------------------- budget


def test_trigger_batch_respects_the_weekly_budget_and_keeps_one_call_back(
    db, world, fake, no_rerun
):
    # 3 trigger calls this week leave the 4th for the weekly review
    for _ in range(3):
        service.create_consultation(
            db, kind="trigger", trigger_info={"type": "lift_stall"}, status="rejected"
        )
    db.commit()
    assert service.calls_this_week(db, TODAY) == 3
    assert service.budget_allows(db, "trigger", TODAY)[0] is False
    assert service.budget_allows(db, "weekly", TODAY)[0] is True
    db.add(
        AdvisorTrigger(
            type="lift_stall",
            dedupe_key="k1",
            subject_type="variant",
            subject_id="1",
            day=TODAY,
            payload={"variant": "Bench"},
        )
    )
    db.commit()
    assert service.run_trigger_batch(db, today=TODAY) is None
    assert fake.calls == []
    assert db.scalar(select(AdvisorTrigger.status)) == "open"  # stays for later


def test_ask_calls_do_not_count_against_the_weekly_budget(db, world, fake, no_rerun):
    for _ in range(6):
        service.create_consultation(db, kind="ask", trigger_info={"type": "ask"}, status="applied")
    db.commit()
    assert service.calls_this_week(db, TODAY) == 0
    assert service.budget_allows(db, "weekly", TODAY)[0]


def test_weekly_review_stops_at_the_max_calls(db, world, fake, no_rerun):
    for _ in range(4):
        service.create_consultation(
            db, kind="trigger", trigger_info={"type": "lift_stall"}, status="applied"
        )
    db.commit()
    assert service.run_weekly_review(db, today=TODAY) is None and fake.calls == []


def test_dry_run_and_awaiting_key_do_not_use_the_budget(db, world, fake, no_rerun):
    service.create_consultation(
        db, kind="weekly", trigger_info={"type": "weekly_review"}, status="dry_run"
    )
    service.create_consultation(
        db, kind="trigger", trigger_info={"type": "lift_stall"}, status="awaiting_key"
    )
    db.commit()
    assert service.calls_this_week(db, TODAY) == 0


def test_max_calls_per_week_is_configurable(db, world, fake, no_rerun):
    cs.create_version(db, {"advisor.max_calls_per_week": 1}, reason="t", author="user")
    db.commit()
    service.create_consultation(
        db, kind="trigger", trigger_info={"type": "lift_stall"}, status="applied"
    )
    db.commit()
    assert service.budget_allows(db, "weekly", TODAY) == (
        False,
        "1 of 1 advisor calls used this week",
    )


def test_ask_limit_per_day(db, world, fake, no_rerun):
    for i in range(5):
        service.start_ask(db, scope="general", ref_id=None, question=f"q{i}")
    with pytest.raises(service.AskLimit) as info:
        service.start_ask(db, scope="general", ref_id=None, question="one more")
    assert "limit of 5" in str(info.value)
    cs.create_version(db, {"advisor.max_ask_per_day": 6}, reason="t", author="user")
    db.commit()
    service.start_ask(db, scope="general", ref_id=None, question="now fine")


# --------------------------------------------------------------------------- triggers + weekly


def _trigger(db, type_, key, **kw):
    t = AdvisorTrigger(
        type=type_,
        dedupe_key=key,
        day=kw.pop("day", TODAY),
        subject_type=kw.pop("subject_type", None),
        subject_id=kw.pop("subject_id", None),
        payload=kw.pop("payload", {}),
    )
    db.add(t)
    db.commit()
    return t


def test_open_triggers_are_batched_into_one_consultation(db, world, fake, no_rerun):
    a = _trigger(
        db,
        "lift_stall",
        "a",
        subject_type="variant",
        subject_id=str(world.bench.id),
        payload={
            "variant": "Bench Press (Barbell)",
            "variant_id": world.bench.id,
            "reason": "flat",
        },
    )
    b = _trigger(db, "rule_conflict", "b", payload={"conflict": "intervals vs heavy lower day"})
    c_ = _trigger(db, "deload", "c", payload={"streak": 4})
    c = service.run_trigger_batch(db, today=TODAY)
    assert c.kind == "trigger" and c.trigger == "lift_stall"  # highest priority leads
    assert "+2 more" in c.trigger_info["title"]
    assert len(fake.calls) == 1
    for t in (a, b, c_):
        db.refresh(t)
        assert t.status == "consulted" and t.consultation_id == c.id
    assert [i["type"] for i in c.trigger_info["items"]] == [
        "lift_stall",
        "rule_conflict",
        "data_anomaly",
    ]
    assert c.request["why"]["items"]  # the input says why
    assert c.trigger_info["ref"] == {"kind": "lift", "id": str(world.bench.id)}
    # nothing left: no second call
    assert service.run_trigger_batch(db, today=TODAY) is None and len(fake.calls) == 1


def test_a_failed_call_releases_its_triggers(db, world, no_rerun, monkeypatch):
    t = _trigger(db, "rule_conflict", "b", payload={"conflict": "x"})
    monkeypatch.setattr(claude, "make_client", lambda s: FakeClient(stop_reason="max_tokens"))
    c = service.run_trigger_batch(db, today=TODAY)
    assert c.status == "failed"
    db.refresh(t)
    assert t.status == "open" and t.consultation_id is None


def test_old_and_recently_consulted_triggers_are_dismissed(db, world, fake, no_rerun):
    from datetime import timedelta

    from apps.fitness.db.types import utcnow

    old = _trigger(db, "rule_conflict", "old", payload={"conflict": "x"})
    old.created_at = utcnow() - timedelta(days=30)
    db.commit()
    assert service.run_trigger_batch(db, today=TODAY) is None
    db.refresh(old)
    assert old.status == "dismissed" and fake.calls == []

    t1 = _trigger(
        db,
        "lift_stall",
        "w1",
        subject_type="variant",
        subject_id="5",
        payload={"variant": "Bench", "variant_id": 5},
    )
    first = service.run_trigger_batch(db, today=TODAY)
    assert first is not None
    again = _trigger(
        db,
        "lift_stall",
        "w2",
        subject_type="variant",
        subject_id="5",
        payload={"variant": "Bench", "variant_id": 5},
    )  # next week's trigger, same lift
    assert service.run_trigger_batch(db, today=TODAY) is None
    db.refresh(again)
    assert again.status == "dismissed" and len(fake.calls) == 1
    db.refresh(t1)


def test_weekly_review_runs_once_per_week_and_takes_open_triggers(db, world, fake, no_rerun):
    t = _trigger(db, "rule_conflict", "b", payload={"conflict": "x"})
    c = service.run_weekly_review(db, today=TODAY)
    assert (
        c.kind == "weekly"
        and c.trigger == "weekly_review"
        and c.week_start.isoformat() == "2026-10-19"
    )
    assert c.trigger_info["ref"] == {"kind": "week", "id": "2026-10-19"}
    assert len(fake.calls) == 1
    db.refresh(t)
    assert t.status == "consulted"
    assert service.run_weekly_review(db, today=TODAY) is None  # one call per week
    assert len(fake.calls) == 1
    from datetime import timedelta

    assert service.run_weekly_review(db, today=TODAY + timedelta(days=7)) is not None


def test_failed_weekly_review_can_be_retried(db, world, no_rerun, monkeypatch):
    monkeypatch.setattr(claude, "make_client", lambda s: FakeClient(stop_reason="max_tokens"))
    assert service.run_weekly_review(db, today=TODAY).status == "failed"
    ok = FakeClient()
    monkeypatch.setattr(claude, "make_client", lambda s: ok)
    assert service.run_weekly_review(db, today=TODAY).status == "pending"


# --------------------------------------------------------------------------- the input


def test_input_summary_is_compact_and_complete(db, world, env):
    c = service.create_consultation(
        db, kind="weekly", trigger_info=service.weekly_trigger_info(TODAY, []), settings=env
    )
    doc = summary.build_input(db, trigger=c.trigger_info, question=None, settings=env)
    text = summary.dumps(doc)
    assert len(text) / 4 < 15000, f"{len(text) / 4:.0f} tokens"
    for key in (
        "why",
        "today",
        "config",
        "program",
        "lifts",
        "readiness_14d",
        "baselines",
        "running",
        "recent_sessions",
        "upcoming_plan",
        "rules",
        "past_advisor_changes",
    ):
        assert key in doc, key
    assert doc["today"] == "2026-10-25" and doc["week_start"] == "2026-10-19"
    assert doc["lifts"]["bench_press"]["stalled"] is True
    assert len(doc["lifts"]["bench_press"]["e1rm_12w"]) <= 12
    assert len(doc["readiness_14d"]) == 14
    assert doc["config"]["params"]["strength.rep_range"] == [7, 12]
    assert doc["program"]["upper"][0][:2] == ["bench_press", "Bench Press (Barbell)"]
    assert doc["running"]["weekly_km_12w"] and doc["running"]["intervals"]
    assert doc["recent_sessions"]


def test_input_fits_the_budget_even_when_forced_small(db, world, env):
    c = service.create_consultation(
        db, kind="weekly", trigger_info=service.weekly_trigger_info(TODAY, []), settings=env
    )
    doc = summary.build_input(db, trigger=c.trigger_info, question=None, settings=env)
    small = summary.fit_budget(json.loads(summary.dumps(doc)), 1200)
    assert summary.estimate_tokens(small) < summary.estimate_tokens(doc)


def test_input_is_deterministic(db, world, env):
    info = service.weekly_trigger_info(TODAY, [])
    a = summary.dumps(summary.build_input(db, trigger=info, question=None, settings=env))
    b = summary.dumps(summary.build_input(db, trigger=info, question=None, settings=env))
    assert a == b


def test_ask_input_carries_the_question_and_focus(db, world, env):
    info = service.ask_trigger_info(db, "lift", str(world.bench.id), "Why is my bench stuck?")
    doc = summary.build_input(db, trigger=info, question="Why is my bench stuck?", settings=env)
    assert doc["why"]["question"] == "Why is my bench stuck?"
    assert doc["why"]["focus"]["name"] == "Bench Press (Barbell)"
    assert doc["why"]["focus"]["sessions"]
    assert "swap_candidates" in doc
    assert "bench_press" in doc["swap_candidates"]  # allowed targets are listed for the asked lift


def test_ask_validates_its_target(db, world):
    for scope, ref in (
        ("lift", "99999"),
        ("lift", "abc"),
        ("run", "nope"),
        ("week", "soon"),
        ("lift", None),
    ):
        with pytest.raises(service.Unprocessable):
            service.ask_trigger_info(db, scope, ref, "q")
    info = service.ask_trigger_info(db, "week", "2026-10-22", "q")
    assert info["ref"] == {"kind": "week", "id": "2026-10-19"}  # normalised to the Monday


def test_stale_running_consultations_are_failed(db, world, no_rerun):
    from datetime import timedelta

    from apps.fitness.db.types import utcnow

    c = service.create_consultation(db, kind="ask", trigger_info={"type": "ask"}, status="running")
    c.updated_at = utcnow() - timedelta(hours=2)
    db.commit()
    assert service.recover_stale(db) == 1
    db.refresh(c)
    assert c.status == "failed" and "Interrupted" in c.error


def test_changes_table_is_the_source_of_the_proposal(db, world, fake, no_rerun):
    c = consult(db)
    rows = list(db.scalars(select(AdvisorChange).where(AdvisorChange.consultation_id == c.id)))
    assert len(rows) == 3 and db.get(AdvisorConsultation, c.id).changes == rows
