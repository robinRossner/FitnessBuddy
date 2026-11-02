"""Compatibility shim, remove in wave 3."""

from apps.fitness.garmin.errors import (
    BadRequestError as BadRequestError,
)
from apps.fitness.garmin.errors import (
    GarminError as GarminError,
)
from apps.fitness.garmin.errors import (
    KeyExpiredError as KeyExpiredError,
)
from apps.fitness.garmin.errors import (
    NotFoundError as NotFoundError,
)
from apps.fitness.garmin.errors import (
    RateLimitedError as RateLimitedError,
)
from apps.fitness.garmin.errors import (
    ServerError as ServerError,
)
from apps.fitness.garmin.errors import (
    SourceUnavailable as SourceUnavailable,
)
