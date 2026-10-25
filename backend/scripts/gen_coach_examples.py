"""Regenerate docs/api/examples/coach_*.json by calling the real coach endpoints (FastAPI
TestClient) against a COPY of the real database, with "today" pinned to 2026-10-25 and Claude
replaced by a canned answer (no network, no API key needed, nothing is written to the live DB).

    cd backend && uv run python -m scripts.gen_coach_examples [--db var/fitness.db]

Walk-through: "Ask coach" about a stalled lift -> a pending proposal with a valid rep-range change,
a valid weekly-volume change and one invalid change (a weight, which is never allowed); a second
question whose low-risk interval change is approved -> an applied proposal and a config version;
a third question about the week ("I missed ...") -> a pending proposal with schedule changes: a
valid move (Friday intervals -> Saturday) and an invalid one (a move into the past).
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import tempfile
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from alembic import command
from alembic.config import Config

TODAY = date(2026, 10, 25)
TOKEN = "example-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
os.environ["ANTHROPIC_API_KEY"] = "sk-ant-example-not-a-real-key"  # before settings are loaded
os.environ["ADVISOR_DRY_RUN"] = "false"
os.environ["ENGINE_AUTO_RUN"] = "false"

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.advisor import claude  # noqa: E402
from app.advisor.schema import (  # noqa: E402
    AdvisorOutput,
    ProposedChange,
    ScheduleChange,
    SessionOverride,
)
from app.analytics import cache  # noqa: E402
from app.api.auth import get_api_token  # noqa: E402
from app.api.routes import get_db  # noqa: E402
from app.db import session as dbsession  # noqa: E402
from app.db.session import make_engine  # noqa: E402
from app.main import app  # noqa: E402
from app.settings import BACKEND_DIR, REPO_ROOT, get_settings  # noqa: E402

OUT = REPO_ROOT / "docs" / "api" / "examples"


def dump(name: str, data) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / name).write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {name} ({len(json.dumps(data)) / 1000:.1f} KB)")


class CannedClaude:
    """The part of anthropic.Anthropic the advisor uses; answers with `self.output`."""

    def __init__(self) -> None:
        self.output: AdvisorOutput | None = None
        self.beta = SimpleNamespace(messages=SimpleNamespace(parse=self._parse))

    def _parse(self, **_kwargs):
        return SimpleNamespace(
            stop_reason="end_turn",
            stop_details=None,
            parsed_output=self.output,
            usage=SimpleNamespace(
                input_tokens=5900,
                output_tokens=3100,
                cache_read_input_tokens=0,
                cache_creation_input_tokens=2300,
                iterations=None,
            ),
            content=[],
            model="claude-opus-5-5",
            _request_id="req_example",
        )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(BACKEND_DIR / "var" / "fitness.db"))
    args = ap.parse_args()

    tmp = Path(tempfile.mkdtemp()) / "copy.db"
    shutil.copy(args.db, tmp)
    url = f"sqlite:///{tmp}"
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    cfg.attributes["url"] = url
    command.upgrade(cfg, "head")
    factory = sessionmaker(bind=make_engine(url), expire_on_commit=False)
    dbsession._factory = factory  # background tasks / the engine re-run use the copy too

    def _db():
        db = factory()
        try:
            yield db
        finally:
            db.close()

    get_settings.cache_clear()
    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_api_token] = lambda: TOKEN
    client = TestClient(app, headers=AUTH)
    canned = CannedClaude()

    # the plan of the example week is stored (schedule changes need planned sessions to target)
    from app.engine import service as engine_service

    with factory() as plan_db:
        engine_service.run_engine(plan_db, today=TODAY, weeks=2)
        plan_db.commit()

    lifts = client.get("/api/v1/stats/lifts").json()["lifts"]
    triceps = next(x for x in lifts if x["name"].startswith("Triceps Extension"))

    with (
        patch("app.analytics.common.today", lambda: TODAY),
        patch("app.engine.service.user_today", lambda settings=None: TODAY),
        patch.object(claude, "make_client", lambda settings: canned),
    ):
        cache.invalidate()
        canned.output = AdvisorOutput(
            diagnosis=(
                "Triceps extension has not improved its best e1RM for several sessions while your "
                "HRV, resting heart rate and sleep are in their normal range, so this looks like a "
                "plateau at the current rep range rather than a recovery problem. Weekly running "
                "volume is well below the 10 % cap, so there is room to build it a little."
            ),
            proposed_changes=[
                ProposedChange(
                    param="exercise_overrides.triceps_extension.rep_range",
                    **{"from": [7, 12]},
                    to=[8, 12],
                    reason=(
                        "A slightly higher floor keeps the load heavier for longer before the next "
                        "load step; check the best e1RM again in 3-4 weeks."
                    ),
                    risk="low",
                ),
                ProposedChange(
                    param="running.weekly_volume_cap_pct",
                    **{"from": 10},
                    to=12,
                    reason="Weekly km have stayed well under the cap for the last 4 weeks.",
                    risk="medium",
                ),
                ProposedChange(
                    param="exercise_overrides.triceps_extension.target_weight",
                    **{"from": 30},
                    to=35,
                    reason="Jump to 35 kg now.",
                    risk="low",
                ),
            ],
            session_overrides=[
                SessionOverride(
                    date="2026-10-26",
                    session="Upper",
                    change="Keep the first triceps set easy to test the new floor.",
                    reason="The first set sets the tone for the other sets.",
                )
            ],
            confidence="medium",
            notes_for_user=(
                "Nothing changes until you approve it. If triceps still stalls in 3-4 weeks, "
                "ask me again."
            ),
        )
        ask1 = client.post(
            "/api/v1/coach/ask",
            json={
                "scope": "lift",
                "ref_id": str(triceps["variant_id"]),
                "question": "My triceps extension has been stuck for weeks. What should I change?",
            },
        )
        assert ask1.status_code == 202, ask1.text
        dump("coach_ask_response.json", ask1.json())

        canned.output = AdvisorOutput(
            diagnosis=(
                "Your last interval session passed with room to spare, and recovery is stable."
            ),
            proposed_changes=[
                ProposedChange(
                    param="running.intervals.reps_max",
                    **{"from": 6},
                    to=7,
                    reason="The last two interval sessions passed at six reps.",
                    risk="low",
                )
            ],
            session_overrides=[],
            confidence="high",
            notes_for_user="One more rep at the top of the progression; the pace is unchanged.",
        )
        ask2 = client.post(
            "/api/v1/coach/ask",
            json={"scope": "general", "ref_id": None, "question": "Are my intervals going well?"},
        ).json()
        approved = client.post(
            f"/api/v1/coach/proposals/{ask2['id']}/approve", json={"change_ids": None}
        )
        assert approved.status_code == 200, approved.text
        dump("coach_approve_response.json", approved.json())

        canned.output = AdvisorOutput(
            diagnosis=(
                "Moving Friday's intervals one day later keeps the 24 h rule after Tuesday's "
                "lower day intact and gives the legs a rest day before them. The move to a day "
                "that has already passed cannot be done."
            ),
            proposed_changes=[],
            session_overrides=[],
            schedule_changes=[
                ScheduleChange(
                    date="2026-10-30",
                    action="move",
                    target="intervals",
                    to_date="2026-10-31",
                    reason="One more day of recovery after the Thursday upper session.",
                ),
                ScheduleChange(
                    date="2026-10-28",
                    action="move",
                    target="easy",
                    to_date="2026-10-24",
                    reason="Do the easy run earlier.",
                ),
            ],
            confidence="medium",
            notes_for_user="Approve the move if Saturday suits you; the second idea was invalid.",
        )
        ask3 = client.post(
            "/api/v1/coach/ask",
            json={
                "scope": "week",
                "ref_id": "2026-10-26",
                "question": "I am tired this week, can Friday's intervals move?",
            },
        )
        assert ask3.status_code == 202, ask3.text
        dump("coach_ask_schedule_response.json", ask3.json())
        dump(
            "coach_proposal_schedule_detail.json",
            client.get(f"/api/v1/coach/proposals/{ask3.json()['id']}").json(),
        )

        pending_id = ask1.json()["id"]
        dump("coach_proposals.json", client.get("/api/v1/coach/proposals?status=all").json())
        dump(
            "coach_proposal_detail.json", client.get(f"/api/v1/coach/proposals/{pending_id}").json()
        )
        dump("coach_history.json", client.get("/api/v1/coach/history").json())
        dump("coach_settings.json", client.get("/api/v1/coach/settings").json())
        plan = client.get("/api/v1/plan/today").json()
        assert plan["coach"]["pending_count"] == 2, plan["coach"]
        dump("coach_plan_today.json", plan)

    app.dependency_overrides.clear()
    shutil.rmtree(tmp.parent, ignore_errors=True)


if __name__ == "__main__":
    main()
