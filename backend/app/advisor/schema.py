"""The strict JSON Claude must answer with (structured outputs: `output_format=AdvisorOutput`).

    {"diagnosis": str,
     "proposed_changes": [{"param": "dotted.config.path", "from": <json>, "to": <json>,
                           "reason": str, "risk": "low|medium|high"}],
     "session_overrides": [{"date": "YYYY-MM-DD", "session": str, "change": str, "reason": str}],
     "confidence": "low|medium|high", "notes_for_user": str}

`from` / `to` are JSON scalars or a flat list (a rep range). The risk Claude states is kept for
the record only: the validator classifies the risk (app/advisor/validator.py).
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


class AdvisorOutput(BaseModel):
    diagnosis: str
    proposed_changes: list[ProposedChange]
    session_overrides: list[SessionOverride]
    confidence: Literal["low", "medium", "high"]
    notes_for_user: str
