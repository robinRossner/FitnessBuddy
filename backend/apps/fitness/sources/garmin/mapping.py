"""Compatibility shim, remove in wave 3."""

from apps.fitness.garmin.mapping import (
    DAILY_PARSERS as DAILY_PARSERS,
)
from apps.fitness.garmin.mapping import (
    MIN_PACE_DISTANCE_M as MIN_PACE_DISTANCE_M,
)
from apps.fitness.garmin.mapping import (
    RUN_TYPES as RUN_TYPES,
)
from apps.fitness.garmin.mapping import (
    activity_from_raw as activity_from_raw,
)
from apps.fitness.garmin.mapping import (
    daily_summary_from_raw as daily_summary_from_raw,
)
from apps.fitness.garmin.mapping import (
    hr_zone_metrics as hr_zone_metrics,
)
from apps.fitness.garmin.mapping import (
    hrv_from_raw as hrv_from_raw,
)
from apps.fitness.garmin.mapping import (
    pace_s_per_km as pace_s_per_km,
)
from apps.fitness.garmin.mapping import (
    parse_garmin_ts as parse_garmin_ts,
)
from apps.fitness.garmin.mapping import (
    sleep_from_raw as sleep_from_raw,
)
from apps.fitness.garmin.mapping import (
    weights_from_range as weights_from_range,
)
