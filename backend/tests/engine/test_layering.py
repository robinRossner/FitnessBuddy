"""Import layering, checked on the source (AST), not on what happens to be in `sys.modules`.

- `apps.fitness.engine.core` is pure: no SQLAlchemy, no `apps.fitness.db`, and from `apps` only
  other `engine.core` modules.
- `apps.fitness.db` may use `engine.core` (enums, config file loading) but nothing else of the
  engine, so `engine.store -> db -> engine.core` stays acyclic.
- `core/` (app-independent) never imports `apps`.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[2]
ENGINE_CORE = BACKEND / "apps" / "fitness" / "engine" / "core"
DB = BACKEND / "apps" / "fitness" / "db"
APP_INDEPENDENT_CORE = BACKEND / "core"


def _module_name(path: Path) -> str:
    rel = path.relative_to(BACKEND).with_suffix("")
    parts = list(rel.parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _imports(path: Path) -> set[str]:
    """Absolute names of every module a source file imports (relative imports resolved)."""
    tree = ast.parse(path.read_text(), filename=str(path))
    pkg = _module_name(path)
    if path.name != "__init__.py":
        pkg = pkg.rpartition(".")[0]
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = pkg.split(".")
                base = base[: len(base) - (node.level - 1)]
                mod = ".".join([*base, node.module] if node.module else base)
            else:
                mod = node.module or ""
            out.add(mod)
            # `from pkg import sub` may import a submodule: count `pkg.sub` too
            out.update(f"{mod}.{a.name}" for a in node.names if a.name != "*")
    return out


def _sources(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*.py") if "__pycache__" not in p.parts)


def _violations(root: Path, bad) -> list[str]:
    return [
        f"{p.relative_to(BACKEND)}: {name}"
        for p in _sources(root)
        for name in sorted(_imports(p))
        if bad(name)
    ]


def _under(name: str, prefix: str) -> bool:
    return name == prefix or name.startswith(prefix + ".")


def test_engine_core_is_pure():
    assert _sources(ENGINE_CORE), "engine/core has no modules"

    def bad(name: str) -> bool:
        if _under(name, "sqlalchemy") or _under(name, "apps.fitness.db"):
            return True
        return _under(name, "apps") and not _under(name, "apps.fitness.engine.core")

    assert _violations(ENGINE_CORE, bad) == []


def test_db_does_not_import_the_engine_beyond_core():
    def bad(name: str) -> bool:
        return _under(name, "apps.fitness.engine") and not (
            _under(name, "apps.fitness.engine.core") or name == "apps.fitness.engine"
        )

    assert _violations(DB, bad) == []


def test_app_independent_core_never_imports_apps():
    assert _violations(APP_INDEPENDENT_CORE, lambda n: _under(n, "apps")) == []


@pytest.mark.parametrize(
    ("src", "expected"),
    [
        ("from apps.fitness.engine.store import data as D", {"apps.fitness.engine.store.data"}),
        ("from sqlalchemy.orm import Session", {"sqlalchemy.orm"}),
        ("from . import ids", {"apps.fitness.engine.core.ids"}),
        ("from ..store import data", {"apps.fitness.engine.store.data"}),
    ],
)
def test_import_scanner(tmp_path, monkeypatch, src, expected):
    """The scanner itself: relative imports resolve, `from pkg import sub` counts the submodule."""
    path = ENGINE_CORE / "_layering_probe.py"
    monkeypatch.setattr(Path, "read_text", lambda self, *a, **k: src)
    assert expected <= _imports(path)


ENGINE = BACKEND / "apps" / "fitness" / "engine"
ENGINE_CORE_PKG = "apps.fitness.engine.core"


def test_engine_top_level_holds_only_core_and_store():
    """The old flat `engine/<module>.py` paths are gone for good: no shims, no new loose modules."""
    entries = {p.name for p in ENGINE.iterdir() if p.name != "__pycache__"}
    assert entries == {"__init__.py", "core", "store"}


def test_store_does_not_re_export_core_helpers():
    """Callers import pure helpers (`planned_id`, `cancel_window`, ...) from `engine.core`, not
    through a store module: no `from apps.fitness.engine.core... import x as x` in
    `engine/store`."""
    found = []
    for path in _sources(ENGINE / "store"):
        for node in ast.walk(ast.parse(path.read_text(), filename=str(path))):
            if isinstance(node, ast.ImportFrom) and _under(node.module or "", ENGINE_CORE_PKG):
                found += [f"{path.name}: {a.name}" for a in node.names if a.asname == a.name]
    assert found == []
