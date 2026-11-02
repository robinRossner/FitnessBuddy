"""A fake Anthropic client for the advisor: records the request and returns (or raises) what the
test sets. Nothing in the test suite ever calls the real API."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from apps.fitness.advisor.schema import AdvisorOutput, ProposedChange, SessionOverride

KEY = "sk-ant-test-0000000000000000"


def make_output(**over: Any) -> AdvisorOutput:
    data: dict[str, Any] = {
        "diagnosis": "Bench press has been flat for four weeks while recovery looks fine.",
        "proposed_changes": [
            ProposedChange(
                param="exercise_overrides.bench_press.rep_range",
                **{"from": [7, 12]},
                to=[8, 12],
                reason="Flat e1RM; a slightly heavier range may break the plateau.",
                risk="low",
            ),
            ProposedChange(
                param="running.weekly_volume_cap_pct",
                **{"from": 10},
                to=12,
                reason="Weekly km are well below the cap.",
                risk="medium",
            ),
            ProposedChange(
                param="running.vdot.seed_race.time_s",
                **{"from": 3150},
                to=2800,
                reason="Pace looks conservative.",
                risk="low",
            ),
        ],
        "session_overrides": [
            SessionOverride(
                date="2026-10-26",
                session="Upper",
                change="Keep the first bench set easy.",
                reason="Shoulder warm-up.",
            )
        ],
        "confidence": "medium",
        "notes_for_user": "Watch bench e1RM over the next three weeks.",
    }
    data.update(over)
    return AdvisorOutput(**data)


class FakeClient:
    """Stands in for anthropic.Anthropic: records the request, returns / raises what it is told."""

    def __init__(
        self,
        output: AdvisorOutput | None = None,
        *,
        stop_reason: str = "end_turn",
        usage: dict[str, int] | None = None,
        exc: Exception | None = None,
        stop_details: Any = None,
    ):
        self.output = output if output is not None else make_output()
        self.stop_reason = stop_reason
        self.usage = usage or {
            "input_tokens": 6000,
            "output_tokens": 2500,
            "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 2200,
        }
        self.exc = exc
        self.stop_details = stop_details
        self.calls: list[dict[str, Any]] = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(parse=self._parse))

    def _parse(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if self.exc is not None:
            raise self.exc
        refused = self.stop_reason in ("refusal", "max_tokens")
        return SimpleNamespace(
            stop_reason=self.stop_reason,
            stop_details=self.stop_details,
            parsed_output=None if refused else self.output,
            usage=SimpleNamespace(iterations=None, **self.usage),
            content=[],
            model="claude-opus-5-5",
            _request_id="req_test_1",
        )
