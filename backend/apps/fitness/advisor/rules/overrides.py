"""Session overrides are advisory notes: they may not prescribe a weight or a pace (also used
for the reasons of schedule changes).
"""

from __future__ import annotations

import re

_WEIGHT_RE = re.compile(r"\d+(?:[.,]\d+)?\s*(?:kg|kgs|lb|lbs)\b", re.I)
_PACE_RE = re.compile(r"\d{1,2}:\d{2}\s*(?:/|per)\s*(?:km|mi)|\d+(?:[.,]\d+)?\s*min\s*/\s*km", re.I)


def override_problem(text: str) -> str | None:
    """Session overrides are advisory notes: they may not prescribe a weight or a pace."""
    if _WEIGHT_RE.search(text):
        return "names a weight"
    if _PACE_RE.search(text):
        return "names a pace"
    return None
