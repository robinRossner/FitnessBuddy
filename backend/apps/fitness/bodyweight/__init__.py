"""Bodyweight log (`service`).

The re-exports below keep the old `apps.fitness.bodyweight` module path working (compatibility
shim, remove in wave 3)."""

from apps.fitness.bodyweight.service import (
    BodyweightTimeline as BodyweightTimeline,
)
from apps.fitness.bodyweight.service import (
    as_aware as as_aware,
)
from apps.fitness.bodyweight.service import (
    bodyweight_at as bodyweight_at,
)
from apps.fitness.bodyweight.service import (
    load_timeline as load_timeline,
)
from apps.fitness.bodyweight.service import (
    local_day as local_day,
)
from apps.fitness.bodyweight.service import (
    manual_id as manual_id,
)
from apps.fitness.bodyweight.service import (
    upsert_measurement as upsert_measurement,
)
