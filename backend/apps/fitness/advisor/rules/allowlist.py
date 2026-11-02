"""The parameter allowlist (`SPECS`, in the order the system prompt prints it), lookup of a
param, and the validation of one proposed change (`validate_change` / `accept`).
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from apps.fitness.advisor.rules import lifting, running
from apps.fitness.advisor.rules.base import Context, Invalid, Spec
from apps.fitness.engine.core.program import Program

SPECS: tuple[Spec, ...] = (
    *lifting.CORE_SPECS,
    *running.CORE_SPECS,
    *lifting.V2_SPECS,
    *running.V2_SPECS,
)


def find_spec(param: str) -> tuple[Spec, dict[str, str]] | None:
    if not isinstance(param, str):
        return None
    for spec in SPECS:
        m = spec.regex.match(param)
        if m:
            return spec, m.groupdict()
    return None


def describe_allowlist() -> str:
    """Deterministic text for the system prompt (the single source of truth is SPECS)."""
    return "\n".join(f"- {s.template}: {s.doc}" for s in SPECS)


@dataclass(frozen=True)
class Verdict:
    valid: bool
    error: str | None
    risk: str | None  # the validator's classification; None for invalid changes
    value: Any  # normalised new value (valid changes)
    current: Any  # what the engine uses today
    label: str


def label_for(spec: Spec, groups: dict[str, str], ctx: Context) -> str:
    if "{name}" not in spec.label:
        return spec.label
    slug = groups.get("slug", "")
    name = None
    entries = ctx.program_entries(slug)
    if entries:
        name = ctx.overrides(slug).get("variant") or entries[0][1].variant
    return spec.label.replace("{name}", name or slug.replace("_", " ").title())


def validate_change(param: str, new_value: Any, ctx: Context) -> Verdict:
    """Validate one proposed change against `ctx` (not modified; use `accept` to chain changes)."""
    found = find_spec(param)
    if found is None:
        return Verdict(
            False,
            "param is not on the allowlist (only engine parameters can be changed, never "
            "weights, paces or heart-rate numbers)",
            None,
            None,
            None,
            str(param)[:120],
        )
    spec, groups = found
    label = label_for(spec, groups, ctx)
    cur = None
    try:
        cur = spec.current(ctx, groups)
        checked = spec.check(new_value, cur, ctx, groups)
        if spec.after is not None:
            trial = ctx.fork()
            trial.apply(param, checked.value)
            problem = spec.after(trial)
            if problem:
                raise Invalid(problem)
    except Invalid as exc:
        return Verdict(False, str(exc), None, None, cur, label)
    return Verdict(True, None, checked.risk, checked.value, cur, label)


def accept(param: str, verdict: Verdict, ctx: Context) -> None:
    """Fold a valid change into `ctx` so the next change is validated against it."""
    ctx.apply(param, verdict.value)


def build_context(
    db: Session,
    content: dict[str, Any],
    *,
    program: Program | None,
    settings=None,
) -> Context:
    """Context with the catalog lookups the swap check needs."""
    from apps.fitness.db.catalog import load_catalog
    from apps.fitness.engine.store import data as D
    from apps.fitness.settings import get_settings

    s = settings or get_settings()
    regions: dict[str, str] = {}
    if s.catalog_path.exists():
        regions = {slug: c.body_region for slug, c in load_catalog(s.catalog_path).items()}
    gym = D.current_gym(db, s.gyms_path)
    return Context(copy.deepcopy(content), program, db, gym, regions)
