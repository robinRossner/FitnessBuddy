"""Compatibility shim, remove in wave 3."""

from apps.fitness.gyms.service import (
    CONFIG_PATH as CONFIG_PATH,
)
from apps.fitness.gyms.service import (
    LADDER_MAX_KG as LADDER_MAX_KG,
)
from apps.fitness.gyms.service import (
    LADDER_MAX_LEN as LADDER_MAX_LEN,
)
from apps.fitness.gyms.service import (
    LADDER_STEP as LADDER_STEP,
)
from apps.fitness.gyms.service import (
    MACHINE_EQUIPMENT as MACHINE_EQUIPMENT,
)
from apps.fitness.gyms.service import (
    GymError as GymError,
)
from apps.fitness.gyms.service import (
    Usage as Usage,
)
from apps.fitness.gyms.service import (
    base_name as base_name,
)
from apps.fitness.gyms.service import (
    configured_current_gym as configured_current_gym,
)
from apps.fitness.gyms.service import (
    configured_gym_id as configured_gym_id,
)
from apps.fitness.gyms.service import (
    create_gym as create_gym,
)
from apps.fitness.gyms.service import (
    create_machine as create_machine,
)
from apps.fitness.gyms.service import (
    delete_gym as delete_gym,
)
from apps.fitness.gyms.service import (
    effective_current_gym as effective_current_gym,
)
from apps.fitness.gyms.service import (
    entry_is_bound as entry_is_bound,
)
from apps.fitness.gyms.service import (
    find_gym as find_gym,
)
from apps.fitness.gyms.service import (
    gym_dict as gym_dict,
)
from apps.fitness.gyms.service import (
    is_bound as is_bound,
)
from apps.fitness.gyms.service import (
    is_user_variant as is_user_variant,
)
from apps.fitness.gyms.service import (
    last_top_set as last_top_set,
)
from apps.fitness.gyms.service import (
    list_gyms as list_gyms,
)
from apps.fitness.gyms.service import (
    machine_count as machine_count,
)
from apps.fitness.gyms.service import (
    machine_item as machine_item,
)
from apps.fitness.gyms.service import (
    machine_item_for as machine_item_for,
)
from apps.fitness.gyms.service import (
    machine_items as machine_items,
)
from apps.fitness.gyms.service import (
    name_taken as name_taken,
)
from apps.fitness.gyms.service import (
    normalize_ladder as normalize_ladder,
)
from apps.fitness.gyms.service import (
    preview_ladder as preview_ladder,
)
from apps.fitness.gyms.service import (
    previous_hint as previous_hint,
)
from apps.fitness.gyms.service import (
    previous_variant as previous_variant,
)
from apps.fitness.gyms.service import (
    program_entries as program_entries,
)
from apps.fitness.gyms.service import (
    rename_gym as rename_gym,
)
from apps.fitness.gyms.service import (
    set_current_gym as set_current_gym,
)
from apps.fitness.gyms.service import (
    unmapped_count as unmapped_count,
)
from apps.fitness.gyms.service import (
    update_variant as update_variant,
)
from apps.fitness.gyms.service import (
    usage_by_variant as usage_by_variant,
)
from apps.fitness.gyms.service import (
    user_variant_key as user_variant_key,
)
from apps.fitness.gyms.service import (
    variant_at_gym as variant_at_gym,
)
from apps.fitness.gyms.service import (
    variants_by_exercise as variants_by_exercise,
)
