"""The strict JSON Claude must answer with (structured outputs: `output_format=AdvisorOutput`).

    {"diagnosis": str,
     "proposed_changes": [{"param": "dotted.config.path", "from": <json>, "to": <json>,
                           "reason": str, "risk": "low|medium|high"}],
     "session_overrides": [{"date": "YYYY-MM-DD", "session": str, "change": str, "reason": str}],
     "schedule_changes": [{"date": "YYYY-MM-DD", "action": "move|skip|swap|shorten|replace",
                           "target": str, "to_date": "YYYY-MM-DD"|null,
                           "new_kind": "easy|rest|upper|lower|intervals|long"|null,
                           "distance_km": number|null, "reason": str}],
     "confidence": "low|medium|high", "notes_for_user": str}

`from` / `to` are JSON scalars or a flat list (a rep range). The risk Claude states is kept for
the record only: the validator classifies the risk (apps/fitness/advisor/validator.py).

`schedule_changes` move / skip / swap / shorten / replace planned sessions of the current and next
week (validated against the hard scheduling rules, applied on approval); `session_overrides`
stay free-text notes that are never applied.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

JsonValue = int | float | str | bool | list[int | float | str] | None


class ProposedChange(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    param: str = Field(description="dotted config path from the allowlist")
    from_: JsonValue = Field(alias="from", description="the current value of the parameter")
    to: JsonValue = Field(description="the proposed new value")
    reason: str = Field(description="one or two sentences, grounded in the input data")
    risk: Literal["low", "medium", "high"]


class SessionOverride(BaseModel):
    date: str = Field(description="YYYY-MM-DD of a planned session")
    session: str = Field(description="which planned session, e.g. 'Upper' or 'Intervals'")
    change: str = Field(description="advisory note; never a weight or a pace")
    reason: str


class ScheduleChange(BaseModel):
    date: str = Field(description="YYYY-MM-DD of the planned session this change is about")
    action: Literal["move", "skip", "swap", "shorten", "replace"]
    target: str = Field(
        description="kind of the planned session on `date`: upper, lower, easy, intervals or long"
    )
    to_date: str | None = Field(
        default=None, description="YYYY-MM-DD, for move and swap (same week), else null"
    )
    new_kind: Literal["easy", "rest", "upper", "lower", "intervals", "long"] | None = Field(
        default=None, description="for replace only: the kind the session becomes, else null"
    )
    distance_km: float | None = Field(
        default=None, description="for shorten only: the new run distance in km, else null"
    )
    reason: str = Field(description="one or two sentences; never a weight or a pace")


class AdvisorOutput(BaseModel):
    diagnosis: str
    proposed_changes: list[ProposedChange]
    session_overrides: list[SessionOverride]
    schedule_changes: list[ScheduleChange] = Field(default_factory=list)
    confidence: Literal["low", "medium", "high"]
    notes_for_user: str
