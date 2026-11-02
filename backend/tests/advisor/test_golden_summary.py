"""Golden trip-wire for the coach input (`advisor.summary.build_input`) on the shared world, for a
weekly review after an engine run. If intended, rerun with UPDATE_GOLDEN=1 and review the diff."""

from __future__ import annotations

import json

from apps.fitness.advisor import service, summary
from apps.fitness.engine import service as engine
from tests.support.golden import assert_golden
from tests.support.world import TODAY


def test_advisor_summary_golden(world, db, env):
    engine.run_engine(db, today=TODAY, settings=env)
    db.commit()
    info = service.weekly_trigger_info(TODAY, [])
    doc = json.loads(
        summary.dumps(summary.build_input(db, trigger=info, question=None, settings=env))
    )
    for rule in doc["rules"]:  # [logged on (wall clock), domain, rule, day, rationale]
        rule[0] = "<logged-on>"
    assert_golden("advisor_summary_weekly", doc)
