"""Compatibility shim, remove in wave 3."""

from apps.fitness.exercises.service import (
    EQUIPMENT as EQUIPMENT,
)
from apps.fitness.exercises.service import (
    EQUIPMENT_LABEL as EQUIPMENT_LABEL,
)
from apps.fitness.exercises.service import (
    INCREMENT_MAX_KG as INCREMENT_MAX_KG,
)
from apps.fitness.exercises.service import (
    MUSCLES as MUSCLES,
)
from apps.fitness.exercises.service import (
    ExerciseError as ExerciseError,
)
from apps.fitness.exercises.service import (
    add_variant as add_variant,
)
from apps.fitness.exercises.service import (
    check_increment as check_increment,
)
from apps.fitness.exercises.service import (
    create_exercise as create_exercise,
)
from apps.fitness.exercises.service import (
    generic_variant_key as generic_variant_key,
)
from apps.fitness.exercises.service import (
    is_user_generic_variant as is_user_generic_variant,
)
from apps.fitness.exercises.service import (
    update_exercise as update_exercise,
)
from apps.fitness.exercises.service import (
    variant_name as variant_name,
)
