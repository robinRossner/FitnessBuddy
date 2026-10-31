"""Small helpers shared by the stats modules: today, week buckets, JSON-safe numbers, Pearson."""

from __future__ import annotations

import math
from collections.abc import Sequence
from datetime import date, timedelta
from statistics import fmean
from typing import Any

from apps.fitness.db.types import iso_z, utcnow
from apps.fitness.engine import service
from apps.fitness.settings import get_settings


def today() -> date:
    """The user's calendar day (settings.user_tz). One seam, so fixtures can pin it."""
    return service.user_today(get_settings())


def generated_at() -> str:
    return iso_z(utcnow()) or ""


def week_start(d: date) -> date:
    return d - timedelta(days=d.weekday())


def week_starts(weeks: int, end: date) -> list[date]:
    """`weeks` Mondays, oldest first, the last one being the week containing `end`."""
    last = week_start(end)
    return [last - timedelta(weeks=weeks - 1 - i) for i in range(weeks)]


def num(x: Any, nd: int | None = 2) -> float | None:
    """JSON-safe number: None for None / NaN / inf, else rounded to `nd` digits."""
    if x is None:
        return None
    x = float(x)
    if math.isnan(x) or math.isinf(x):
        return None
    return round(x, nd) if nd is not None else x


def mean(xs: Sequence[float]) -> float | None:
    return fmean(xs) if xs else None


def pearson(xs: Sequence[float], ys: Sequence[float], *, min_n: int = 5) -> float | None:
    """Pearson r, or None when n < `min_n` or either series is constant."""
    n = len(xs)
    if n < min_n or n != len(ys):
        return None
    mx, my = fmean(xs), fmean(ys)
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if sxx <= 1e-12 or syy <= 1e-12:
        return None
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True))
    return max(-1.0, min(1.0, sxy / math.sqrt(sxx * syy)))
