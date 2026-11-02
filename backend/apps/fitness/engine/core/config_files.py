"""Reading the YAML config files (engine config, program, exercise catalog), cached by file state.

A leaf module: nothing from `apps` is imported, so `db` (seeding) can use it without pulling in the
engine. A parsed file is cached under (absolute path, mtime_ns, size): editing or replacing the
file changes the key, so the next read parses it again; `clear_config_caches()` drops everything
(CLI reloads, tests).
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
from collections.abc import Callable
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

FileKey = tuple[str, int, int]

_extra_caches: list[Callable[[], None]] = []


def file_key(path: str | os.PathLike[str]) -> FileKey:
    """Cache key of a file as it is on disk now: (absolute path, mtime_ns, size)."""
    p = os.path.abspath(os.fspath(path))
    st = os.stat(p)
    return (p, st.st_mtime_ns, st.st_size)


@lru_cache(maxsize=64)
def _parse(key: FileKey) -> Any:
    return yaml.safe_load(Path(key[0]).read_text(encoding="utf-8"))


def cached_yaml(path: str | os.PathLike[str]) -> Any:
    """The parsed YAML of `path`, shared between callers: read it, never mutate it (`load_raw`
    hands out a private copy)."""
    return _parse(file_key(path))


def load_raw(path: str | os.PathLike[str]) -> dict[str, Any]:
    """The parsed YAML of `path` as a fresh dict the caller may change."""
    return raw_at(file_key(path))


def raw_at(key: FileKey) -> Any:
    """A private copy of the parsed file under `key` (for caches keyed on `file_key`)."""
    return copy.deepcopy(_parse(key))


def content_hash(raw: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(raw, sort_keys=True).encode()).hexdigest()


def register_cache(clear: Callable[[], None]) -> None:
    """Have `clear_config_caches()` also clear a cache built on top of these files."""
    _extra_caches.append(clear)


def clear_config_caches() -> None:
    """Forget every parsed config file (and the caches built from them)."""
    _parse.cache_clear()
    for clear in _extra_caches:
        clear()
