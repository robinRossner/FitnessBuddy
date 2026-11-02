"""Golden-file comparison: `assert_golden(name, obj)` against `tests/golden/<name>.json`.

Run with `UPDATE_GOLDEN=1` to (re)write the files after an intended output change; review the diff.
Volatile values (wall-clock timestamps, random UUIDs) are normalised before comparing.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

GOLDEN_DIR = Path(__file__).resolve().parent.parent / "golden"

# Random (version 4) UUIDs differ per run; name-based ones (uuid5, the planned-session ids) do not.
_UUID4 = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
# Wall-clock stamps written by the code under test (created_at, updated_at, computed_at, ...).
_STAMP_KEYS = frozenset({"created_at", "updated_at", "computed_at", "generated_at"})


def normalise(obj: Any) -> Any:
    """Replace values that change from run to run with stable placeholders."""
    uuids: dict[str, str] = {}

    def walk(x: Any, key: str | None = None) -> Any:
        if isinstance(x, dict):
            return {k: walk(v, str(k)) for k, v in x.items()}
        if isinstance(x, list | tuple):
            return [walk(v) for v in x]
        if isinstance(x, str):
            if _UUID4.match(x):
                return uuids.setdefault(x, f"<uuid4:{len(uuids)}>")
            if key in _STAMP_KEYS:
                return "<timestamp>"
        return x

    return walk(obj)


def dumps(obj: Any) -> str:
    return json.dumps(normalise(obj), sort_keys=True, indent=2, default=str) + "\n"


def assert_golden(name: str, obj: Any) -> None:
    path = GOLDEN_DIR / f"{name}.json"
    text = dumps(obj)
    if os.environ.get("UPDATE_GOLDEN") == "1":
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return
    assert path.exists(), f"no golden file {path.name}; run with UPDATE_GOLDEN=1 to create it"
    expected = path.read_text(encoding="utf-8")
    if text != expected:
        import difflib

        diff = "".join(
            difflib.unified_diff(
                expected.splitlines(keepends=True),
                text.splitlines(keepends=True),
                f"golden/{path.name}",
                "actual",
                n=2,
            )
        )
        raise AssertionError(
            f"output differs from golden/{path.name} (UPDATE_GOLDEN=1 rewrites it):\n" + diff[:8000]
        )
