"""Coach API (docs/api/coach.md) end to end with a mocked Claude client."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from apps.fitness.main import app
from tests.advisor_helpers import (
    AUTH,
    KEY,
    configure,
)

ENDPOINTS = [
    ("GET", "/api/v1/coach/proposals"),
    ("GET", "/api/v1/coach/proposals/1"),
    ("POST", "/api/v1/coach/proposals/1/approve"),
    ("POST", "/api/v1/coach/proposals/1/reject"),
    ("POST", "/api/v1/coach/ask"),
    ("GET", "/api/v1/coach/history"),
    ("GET", "/api/v1/coach/settings"),
    ("PUT", "/api/v1/coach/settings"),
]

PROPOSAL_KEYS = {
    "id", "created_at", "status", "trigger", "question", "diagnosis", "notes_for_user",
    "confidence", "changes", "schedule_changes", "session_overrides", "applied_config_version",
    "error", "cost_usd",
}  # fmt: skip
CHANGE_KEYS = {
    "id", "param", "label", "from", "to", "reason", "risk", "valid", "validation_error", "status",
}  # fmt: skip
SCHEDULE_CHANGE_KEYS = {
    "id", "date", "action", "target", "to_date", "new_kind", "distance_km", "reason", "risk",
    "valid", "validation_error", "status", "label",
}  # fmt: skip


def ask(client, **body):
    body = {"scope": "general", "ref_id": None, "question": "How is my training going?", **body}
    return client.post("/api/v1/coach/ask", json=body)


def test_every_endpoint_requires_the_bearer_token(world, session_factory):
    anon = TestClient(app)
    for method, path in ENDPOINTS:
        assert anon.request(method, path).status_code == 401, path
        r = anon.request(method, path, headers={"Authorization": "Bearer nope"})
        assert r.status_code == 401, path


def test_ask_runs_in_the_background_and_returns_202(client):
    r = ask(client, question="Why is my bench stuck?")
    assert r.status_code == 202
    p = r.json()
    assert set(p) == PROPOSAL_KEYS
    assert p["status"] == "queued" and p["question"] == "Why is my bench stuck?"
    assert p["trigger"]["type"] == "ask" and p["trigger"]["ref"] == {"kind": None, "id": None}
    # the background task has run by the time the response is done (TestClient)
    got = client.get(f"/api/v1/coach/proposals/{p['id']}").json()
    assert got["status"] == "pending" and got["diagnosis"] and got["confidence"] == "medium"
    assert (
        "input_summary" in got
        and got["input_summary"]["why"]["question"] == "Why is my bench stuck?"
    )
    assert set(got) == PROPOSAL_KEYS | {"input_summary"}


def test_ask_about_a_lift_a_run_and_a_week(client, world):
    r = ask(client, scope="lift", ref_id=str(world.bench.id)).json()
    assert r["trigger"]["ref"] == {"kind": "lift", "id": str(world.bench.id)}
    assert "Bench Press" in r["trigger"]["title"]
    r = ask(client, scope="week", ref_id="2026-10-22").json()
    assert r["trigger"]["ref"] == {"kind": "week", "id": "2026-10-19"}
    run_id = client.get("/api/v1/stats/running").json()["intervals"][0]["run_id"]
    r = ask(client, scope="run", ref_id=run_id).json()
    assert r["trigger"]["ref"] == {"kind": "run", "id": run_id}


@pytest.mark.parametrize(
    "body",
    [
        {"scope": "lift", "ref_id": "999999"},
        {"scope": "lift", "ref_id": None},
        {"scope": "run", "ref_id": "nope"},
        {"scope": "week", "ref_id": "soon"},
        {"scope": "bogus"},
        {"question": ""},
        {"question": "   "},
        {"question": "x" * 2001},
    ],
)
def test_ask_validates_its_body(client, body):
    assert ask(client, **body).status_code == 422


def test_ask_is_rate_limited_per_day(client):
    for i in range(5):
        assert ask(client, question=f"q{i}").status_code == 202
    r = ask(client, question="one too many")
    assert r.status_code == 429 and "limit of 5" in r.json()["detail"]


def test_ask_without_a_key_is_awaiting_key_and_does_not_crash(
    db, monkeypatch, tmp_path, session_factory, pin_session_factory, no_rerun
):
    from apps.fitness.api.routes import get_db
    from tests.advisor_helpers import build_world

    configure(monkeypatch, tmp_path, key=None)
    build_world(db, monkeypatch)

    def _db():
        s = session_factory()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = _db
    c = TestClient(app, headers=AUTH)
    r = ask(c)
    assert r.status_code == 202 and r.json()["status"] == "awaiting_key"
    got = c.get(f"/api/v1/coach/proposals/{r.json()['id']}").json()
    assert got["status"] == "awaiting_key" and got["input_summary"]["why"]
    assert got["changes"] == [] and got["error"] is None
    s = c.get("/api/v1/coach/settings").json()
    assert s["api_key_configured"] is False
    assert c.get("/api/v1/coach/proposals?status=pending").json() == {"items": []}
    assert len(c.get("/api/v1/coach/proposals").json()["items"]) == 1


def test_proposals_list_and_status_filters(client, db):
    ask(client, question="one")
    ask(client, question="two")
    items = client.get("/api/v1/coach/proposals").json()["items"]
    assert [i["question"] for i in items] == ["two", "one"]  # newest first
    for i in items:
        assert set(i) == PROPOSAL_KEYS and i["status"] == "pending"
        for ch in i["changes"]:
            assert set(ch) == CHANGE_KEYS
    assert len(client.get("/api/v1/coach/proposals?status=pending").json()["items"]) == 2
    assert client.get("/api/v1/coach/proposals?status=applied").json()["items"] == []
    pid = items[0]["id"]
    client.post(f"/api/v1/coach/proposals/{pid}/reject", json={"note": "no"})
    assert [
        i["id"] for i in client.get("/api/v1/coach/proposals?status=rejected").json()["items"]
    ] == [pid]
    assert len(client.get("/api/v1/coach/proposals?status=pending").json()["items"]) == 1
    assert len(client.get("/api/v1/coach/proposals?status=all").json()["items"]) == 2
    assert client.get("/api/v1/coach/proposals?status=bogus").status_code == 422
    assert len(client.get("/api/v1/coach/proposals?limit=1").json()["items"]) == 1


def test_approve_subset_then_rest_and_history(client):
    p = ask(client).json()
    got = client.get(f"/api/v1/coach/proposals/{p['id']}").json()
    by_param = {c["param"]: c for c in got["changes"]}
    rep = by_param["exercise_overrides.bench_press.rep_range"]
    assert rep["valid"] and rep["risk"] == "low" and rep["from"] == [7, 12] and rep["to"] == [8, 12]
    assert rep["label"] == "Bench Press (Barbell) rep range" and rep["status"] == "proposed"
    bad = by_param["running.vdot.seed_race.time_s"]
    assert (
        bad["valid"] is False
        and bad["status"] == "invalid"
        and "allowlist" in bad["validation_error"]
    )

    r = client.post(f"/api/v1/coach/proposals/{p['id']}/approve", json={"change_ids": [rep["id"]]})
    assert r.status_code == 200
    out = r.json()
    assert out["status"] == "partially_applied" and out["applied_config_version"] == "v3.1"
    statuses = {c["param"]: c["status"] for c in out["changes"]}
    assert statuses["exercise_overrides.bench_press.rep_range"] == "applied"
    assert statuses["running.weekly_volume_cap_pct"] == "proposed"

    # idempotent
    again = client.post(
        f"/api/v1/coach/proposals/{p['id']}/approve", json={"change_ids": [rep["id"]]}
    )
    assert again.status_code == 200 and again.json() == out

    # null = all valid ones
    done = client.post(
        f"/api/v1/coach/proposals/{p['id']}/approve", json={"change_ids": None}
    ).json()
    assert done["status"] == "applied" and done["applied_config_version"] == "v3.2"

    hist = client.get("/api/v1/coach/history").json()["versions"]
    assert [v["version"] for v in hist][:3] == ["v3.2", "v3.1", "v3"]  # newest first
    top = hist[0]
    assert set(top) == {
        "version",
        "created_at",
        "author",
        "reason",
        "diff",
        "consultation_id",
        "outcome",
        "active",
    }
    assert (
        top["author"] == "advisor" and top["active"] is True and top["consultation_id"] == p["id"]
    )
    assert top["diff"] == {"running.weekly_volume_cap_pct": {"old": 10, "new": 12}}
    assert top["outcome"] is None and top["created_at"].endswith("Z")
    assert hist[-1]["author"] == "seed" and hist[-1]["consultation_id"] is None
    assert [v["active"] for v in hist].count(True) == 1


def test_approve_without_a_body_means_all_valid(client):
    p = ask(client).json()
    r = client.post(f"/api/v1/coach/proposals/{p['id']}/approve")
    assert r.status_code == 200 and r.json()["status"] == "applied"


def test_approve_triggers_the_engine_rerun_once(client, no_rerun):
    p = ask(client).json()
    client.post(f"/api/v1/coach/proposals/{p['id']}/approve", json={"change_ids": None})
    client.post(f"/api/v1/coach/proposals/{p['id']}/approve", json={"change_ids": None})
    assert no_rerun == ["rerun"]


def test_approve_errors(client):
    assert client.post("/api/v1/coach/proposals/999/approve", json={}).status_code == 404
    assert client.get("/api/v1/coach/proposals/abc").status_code == 404
    p = ask(client).json()
    got = client.get(f"/api/v1/coach/proposals/{p['id']}").json()
    invalid = next(c for c in got["changes"] if not c["valid"])
    r = client.post(
        f"/api/v1/coach/proposals/{p['id']}/approve", json={"change_ids": [invalid["id"]]}
    )
    assert r.status_code == 422
    assert (
        client.post(
            f"/api/v1/coach/proposals/{p['id']}/approve", json={"change_ids": ["999"]}
        ).status_code
        == 422
    )
    client.post(f"/api/v1/coach/proposals/{p['id']}/reject", json={})
    assert client.post(f"/api/v1/coach/proposals/{p['id']}/approve", json={}).status_code == 409


def test_reject(client):
    p = ask(client).json()
    r = client.post(f"/api/v1/coach/proposals/{p['id']}/reject", json={"note": "not now"})
    assert r.status_code == 200
    assert r.json()["status"] == "rejected"
    assert {c["status"] for c in r.json()["changes"]} == {"rejected", "invalid"}
    # idempotent, body optional
    assert client.post(f"/api/v1/coach/proposals/{p['id']}/reject").json() == r.json()
    assert client.post("/api/v1/coach/proposals/999/reject").status_code == 404
    # nothing was applied
    assert client.get("/api/v1/coach/history").json()["versions"][0]["version"] == "v3"


def test_failed_consultation_shows_its_error(client, fake):
    fake.stop_reason = "max_tokens"
    p = ask(client).json()
    got = client.get(f"/api/v1/coach/proposals/{p['id']}").json()
    assert got["status"] == "failed" and "cut off" in got["error"]
    assert got["changes"] == [] and got["cost_usd"] is not None
    assert client.post(f"/api/v1/coach/proposals/{p['id']}/approve").status_code == 409
    assert client.post(f"/api/v1/coach/proposals/{p['id']}/reject").json()["status"] == "rejected"


def test_settings_get_and_put(client):
    s = client.get("/api/v1/coach/settings").json()
    assert s == {
        "approval_mode": "manual",
        "api_key_configured": True,
        "dry_run": False,
        "calls_this_week": 0,
        "max_calls_per_week": 4,
        "provider": "api",
        "requested_provider": "auto",
        "credential_configured": True,
        "billing": "api",
        "model": s["model"],
    }
    assert s["model"].startswith("claude")
    r = client.put("/api/v1/coach/settings", json={"approval_mode": "auto-low-risk"})
    assert r.status_code == 200 and r.json()["approval_mode"] == "auto-low-risk"
    hist = client.get("/api/v1/coach/history").json()["versions"][0]
    assert hist["author"] == "user" and hist["diff"] == {
        "advisor.approval_mode": {"old": None, "new": "auto-low-risk"}
    }
    assert client.put("/api/v1/coach/settings", json={"approval_mode": "yolo"}).status_code == 422
    assert client.put("/api/v1/coach/settings", json={}).status_code == 422
    # the key itself is never part of any response
    assert KEY not in client.get("/api/v1/coach/settings").text


def test_ask_with_auto_low_risk_applies_the_low_risk_change(client, no_rerun):
    client.put("/api/v1/coach/settings", json={"approval_mode": "auto-low-risk"})
    p = ask(client).json()
    got = client.get(f"/api/v1/coach/proposals/{p['id']}").json()
    assert got["status"] == "partially_applied"
    assert got["applied_config_version"] == "v3.2"  # v3.1 was the settings change
    assert no_rerun == ["rerun"]
    # still waiting for the user: the high-risk change
    assert client.get("/api/v1/coach/proposals?status=pending").json()["items"][0]["id"] == p["id"]


def test_plan_today_has_the_coach_block(client):
    base = client.get("/api/v1/plan/today").json()
    assert base["coach"] == {"pending_count": 0, "latest_pending_id": None}
    first = ask(client).json()
    second = ask(client).json()
    block = client.get("/api/v1/plan/today").json()["coach"]
    assert block == {"pending_count": 2, "latest_pending_id": second["id"]}
    client.post(f"/api/v1/coach/proposals/{second['id']}/reject")
    assert client.get("/api/v1/plan/today").json()["coach"] == {
        "pending_count": 1,
        "latest_pending_id": first["id"],
    }
    # additive: the old keys are still there
    assert {"date", "readiness", "readiness_today", "sessions"} <= set(base)


def test_dry_run_setting_is_reported_and_nothing_is_called(
    db, monkeypatch, tmp_path, session_factory, pin_session_factory, no_rerun, fake
):
    from apps.fitness.api.routes import get_db
    from tests.advisor_helpers import build_world

    configure(monkeypatch, tmp_path, advisor_dry_run="true")
    build_world(db, monkeypatch)

    def _db():
        s = session_factory()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = _db
    c = TestClient(app, headers=AUTH)
    assert c.get("/api/v1/coach/settings").json()["dry_run"] is True
    p = ask(c).json()
    got = c.get(f"/api/v1/coach/proposals/{p['id']}").json()
    assert got["status"] == "dry_run" and got["input_summary"]["lifts"]
    assert fake.calls == []
