"""Gym periods (data/catalog/gyms.yaml): which gym a session belongs to, by date.

File format (see the yaml for the real thing)::

    gyms:
      - {key: gym1, name: Gym 1}                     # first gym: no start = since forever
      - {key: gym2, name: Gym 2, start: 2024-09-02}  # first session at this gym
      - {key: gym2, name: Gym 2, start: 2026-08-17, same_as: gym1}   # optional: same place

Optional per-date overrides for days that do not fit the period (e.g. one visit to the old gym)::

    overrides:
      - {date: 2026-08-30, gym: gym2, note: lower day at the old gym}

An override applies to every session on that local date. `gym` is a key or name.

A gym is in use from its `start` (inclusive) until the next gym's `start`. `key` is the stable
identity (rename `name` freely). `same_as` (a key or a name) merges a period into another gym:
sessions and machine/cable variants then belong to that gym, as if it was never left.
"""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass
from datetime import date
from functools import lru_cache
from pathlib import Path

import yaml

# Equipment whose weights depend on the physical machine (pulley ratios, stack steps, sled mass).
# Barbell / dumbbell / bodyweight are the same everywhere.
GYM_BOUND_EQUIPMENT = frozenset({"machine_stack", "cable", "smith", "plate_loaded"})


class GymConfigError(ValueError):
    pass


@dataclass(frozen=True)
class GymSpec:
    key: str
    name: str
    start: date | None = None
    same_as: str | None = None  # key or name of another gym
    timezone: str | None = None
    notes: str | None = None


class GymPeriods:
    def __init__(self, specs: list[GymSpec], overrides: dict[date, str] | None = None):
        if not specs:
            raise GymConfigError("gyms: at least one gym is required")
        keys = [g.key for g in specs]
        names = [g.name for g in specs]
        if len(set(keys)) != len(keys):
            raise GymConfigError("gyms: duplicate key")
        if len(set(names)) != len(names):
            raise GymConfigError("gyms: duplicate name")
        later = [g.start for g in specs[1:]]
        if any(d is None for d in later):
            raise GymConfigError("gyms: only the first gym may omit `start`")
        chain = ([specs[0].start] if specs[0].start else []) + later
        if chain != sorted(set(chain)):
            raise GymConfigError("gyms: `start` dates must be strictly increasing")
        self.specs = specs
        self._by_ref = {**{g.name: g for g in specs}, **{g.key: g for g in specs}}
        self._canonical: dict[str, GymSpec] = {g.key: self._follow(g) for g in specs}
        self._starts = later  # sorted, none None
        self.overrides: dict[date, GymSpec] = {}
        for day, ref in (overrides or {}).items():
            target = self._by_ref.get(ref)
            if target is None:
                raise GymConfigError(f"gyms: override {day}: {ref!r} is not a gym")
            self.overrides[day] = self._canonical[target.key]

    def _follow(self, gym: GymSpec) -> GymSpec:
        seen = {gym.key}
        cur = gym
        while cur.same_as:
            nxt = self._by_ref.get(cur.same_as)
            if nxt is None:
                raise GymConfigError(f"gyms: {cur.key}: same_as {cur.same_as!r} is not a gym")
            if nxt.key in seen:
                raise GymConfigError(f"gyms: same_as loop at {nxt.key}")
            seen.add(nxt.key)
            cur = nxt
        return cur

    def period_gym(self, day: date) -> GymSpec:
        """The gym as written in the file (before `same_as` merging)."""
        return self.specs[bisect_right(self._starts, day)]

    def resolve(self, day: date) -> GymSpec:
        """The canonical gym for a session on `day` (override, else period; after `same_as`)."""
        override = self.overrides.get(day)
        if override is not None:
            return override
        return self._canonical[self.period_gym(day).key]

    @property
    def canonical_gyms(self) -> list[GymSpec]:
        """Distinct canonical gyms in file order."""
        seen: dict[str, GymSpec] = {}
        for g in self.specs:
            c = self._canonical[g.key]
            seen.setdefault(c.key, c)
        return list(seen.values())

    @property
    def latest(self) -> GymSpec:
        return self._canonical[self.specs[-1].key]

    @classmethod
    def single(cls, name: str, key: str = "main") -> GymPeriods:
        return cls([GymSpec(key=key, name=name)])


def _as_date(value: object, where: str) -> date | None:
    if value is None:
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except ValueError as exc:
        raise GymConfigError(f"{where}: bad date {value!r}") from exc


def load_gyms(path: Path) -> GymPeriods:
    """The parsed gyms file, cached until the file changes (path, mtime, size). Treat the result
    as read-only: it is shared between callers."""
    st = Path(path).stat()
    return _load_gyms_cached(Path(path), st.st_mtime_ns, st.st_size)


def clear_gyms_cache() -> None:
    """Forget every parsed gyms file (the CLI after an edit within the same second; tests)."""
    _load_gyms_cached.cache_clear()


@lru_cache(maxsize=8)
def _load_gyms_cached(path: Path, _mtime_ns: int, _size: int) -> GymPeriods:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    specs: list[GymSpec] = []
    for i, item in enumerate(data.get("gyms") or []):
        where = f"gyms[{i}]"
        if not item.get("key") or not item.get("name"):
            raise GymConfigError(f"{where}: `key` and `name` are required")
        specs.append(
            GymSpec(
                key=str(item["key"]),
                name=str(item["name"]),
                start=_as_date(item.get("start"), where),
                same_as=str(item["same_as"]) if item.get("same_as") else None,
                timezone=item.get("timezone"),
                notes=item.get("notes"),
            )
        )
    overrides: dict[date, str] = {}
    for i, item in enumerate(data.get("overrides") or []):
        where = f"overrides[{i}]"
        day = _as_date(item.get("date"), where)
        if day is None or not item.get("gym"):
            raise GymConfigError(f"{where}: `date` and `gym` are required")
        if day in overrides:
            raise GymConfigError(f"{where}: duplicate override for {day}")
        overrides[day] = str(item["gym"])
    return GymPeriods(specs, overrides)
