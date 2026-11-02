"""Gyms: the gym periods file (`catalog`) and the gym / machine service (`service`).

The re-exports below keep the old `apps.fitness.gyms` module path working (compatibility shim,
remove in wave 3)."""

from apps.fitness.gyms.catalog import (
    GYM_BOUND_EQUIPMENT as GYM_BOUND_EQUIPMENT,
)
from apps.fitness.gyms.catalog import (
    GymConfigError as GymConfigError,
)
from apps.fitness.gyms.catalog import (
    GymPeriods as GymPeriods,
)
from apps.fitness.gyms.catalog import (
    GymSpec as GymSpec,
)
from apps.fitness.gyms.catalog import (
    load_gyms as load_gyms,
)
