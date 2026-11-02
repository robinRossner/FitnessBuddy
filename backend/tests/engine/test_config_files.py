"""Config files are parsed once per file state: same (mtime_ns, size) -> no new read."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from apps.fitness.db.catalog import load_catalog
from apps.fitness.engine.core import config_files as CF
from apps.fitness.engine.core.config import load_engine_config
from apps.fitness.engine.core.program import load_program
from apps.fitness.settings import get_settings


@pytest.fixture
def reads(monkeypatch):
    """Counts `Path.read_text` calls per file name."""
    CF.clear_config_caches()
    counts: dict[str, int] = {}
    real = Path.read_text

    def counting(self, *a, **k):
        counts[self.name] = counts.get(self.name, 0) + 1
        return real(self, *a, **k)

    monkeypatch.setattr(Path, "read_text", counting)
    yield counts
    CF.clear_config_caches()


def _bump_mtime(path: Path) -> None:
    st = path.stat()
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))


def test_second_read_hits_the_cache_and_a_touch_invalidates(tmp_path, reads):
    f = tmp_path / "a.yaml"
    f.write_text("x: 1\n")
    assert CF.cached_yaml(f) == {"x": 1}
    assert CF.cached_yaml(f) == {"x": 1}
    assert reads == {"a.yaml": 1}

    _bump_mtime(f)  # same content, new mtime: parsed again
    CF.cached_yaml(f)
    assert reads == {"a.yaml": 2}


def test_rewrite_with_new_content_is_seen(tmp_path, reads):
    f = tmp_path / "a.yaml"
    f.write_text("x: 1\n")
    assert CF.load_raw(f) == {"x": 1}
    f.write_text("x: 22\n")  # size differs even if the clock did not move
    assert CF.load_raw(f) == {"x": 22}


def test_load_raw_hands_out_private_copies(tmp_path, reads):
    f = tmp_path / "a.yaml"
    f.write_text("x: {y: 1}\n")
    first = CF.load_raw(f)
    first["x"]["y"] = 99
    assert CF.load_raw(f) == {"x": {"y": 1}}
    assert CF.cached_yaml(f) == {"x": {"y": 1}}
    assert reads == {"a.yaml": 1}


def test_clear_config_caches_forces_a_new_read(tmp_path, reads):
    f = tmp_path / "a.yaml"
    f.write_text("x: 1\n")
    CF.cached_yaml(f)
    CF.clear_config_caches()
    CF.cached_yaml(f)
    assert reads == {"a.yaml": 2}


def test_engine_config_is_validated_once_per_file_state(tmp_path, reads):
    src = get_settings().engine_config_path
    f = tmp_path / src.name
    f.write_text(src.read_text())
    reads.clear()

    cfg = load_engine_config(f)
    assert load_engine_config(f) is cfg
    assert reads == {f.name: 1}

    _bump_mtime(f)
    again = load_engine_config(f)
    assert again is not cfg and again == cfg
    assert reads == {f.name: 2}

    CF.clear_config_caches()  # also drops the validated model
    assert load_engine_config(f) is not again


def test_program_and_catalog_read_through_the_cache(tmp_path, reads):
    s = get_settings()
    prog = tmp_path / "program.yaml"
    prog.write_text(
        "generated: {by: test}\ndays:\n  upper:\n    - {exercise: bench_press, variant: Bench}\n"
    )
    cat = tmp_path / "exercises.yaml"
    cat.write_text(s.catalog_path.read_text())
    reads.clear()

    p1, p2 = load_program(prog), load_program(prog)
    assert p1 == p2 and p1 is not p2
    p1.meta["by"] = "changed"  # a caller's Program is its own
    assert load_program(prog).meta == {"by": "test"}
    assert load_catalog(cat) == load_catalog(cat)
    assert reads == {"program.yaml": 1, "exercises.yaml": 1}
