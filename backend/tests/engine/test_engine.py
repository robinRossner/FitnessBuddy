from dataclasses import dataclass

import pytest
from pydantic import ValidationError

from apps.fitness.engine.config import IncrementConfig, load_engine_config
from apps.fitness.engine.increments import next_load
from apps.fitness.engine.pace import PaceModel
from apps.fitness.settings import FITNESS_DIR
from apps.fitness.sources.garmin.base import GarminSource

CONFIG = load_engine_config(FITNESS_DIR / "config" / "engine.v1.yaml")


@dataclass
class V:
    equipment_type: str
    load_convention: str = "total"
    weight_ladder: list[float] | None = None


def test_engine_config_values():
    assert CONFIG.version == "v1" and CONFIG.approval_mode == "manual"
    assert CONFIG.strength.rep_range == (7, 12)
    assert CONFIG.strength.stall_exposures == 3
    hr = CONFIG.running["heart_rate"]
    assert hr["max_hr"] == 192 and hr["lthr"] is None
    assert hr["easy_hr_max"] == int(0.80 * 192) == 153
    assert hr["easy_hr_target_low"] == 128
    assert CONFIG.weekly_template["fri"] == "intervals"


def test_barbell_and_plates():
    assert next_load(V("barbell"), 80, CONFIG) == 85
    assert next_load(V("smith"), 60, CONFIG) == 65
    assert next_load(V("plate_loaded"), 100, CONFIG) == 105


def test_dumbbell_step_2_vs_2_5():
    assert next_load(V("dumbbell", "per_hand"), 28, CONFIG) == 30
    inc25 = IncrementConfig(dumbbell_increment_kg=2.5)
    assert next_load(V("dumbbell", "per_hand"), 27.5, inc25) == 30
    # logged as total of both dumbbells -> step doubles
    assert next_load(V("dumbbell", "total"), 56, CONFIG) == 60
    with pytest.raises(ValidationError):
        IncrementConfig(dumbbell_increment_kg=3.0)


def test_stack_next_rung():
    v = V("machine_stack", weight_ladder=[40, 45, 50, 56.5, 63])
    assert next_load(v, 45, CONFIG) == 50
    assert next_load(v, 47, CONFIG) == 50  # between rungs -> next rung up
    assert next_load(v, 50, CONFIG) == 56.5
    assert next_load(V("cable", weight_ladder=[60.0, 50.0, 55.0]), 50, CONFIG) == 55  # unsorted


def test_stack_above_top_rung_and_no_ladder():
    v = V("cable", weight_ladder=[40, 45, 50])
    assert next_load(v, 50, CONFIG) == 55  # default_stack_step_kg
    assert next_load(v, 52, CONFIG) == 57
    assert next_load(V("machine_stack"), 70, CONFIG) == 75


def test_bodyweight_and_assistance():
    assert next_load(V("bodyweight"), 0, CONFIG) == 2.5
    assisted = V("machine_stack", "assistance", weight_ladder=[10, 15, 20, 25])
    assert next_load(assisted, 20, CONFIG) == 15  # less assistance = harder
    assert next_load(V("bodyweight", "assistance"), 3, CONFIG) == 0


def test_protocols_are_importable_stubs():
    assert hasattr(GarminSource, "activities") and hasattr(GarminSource, "daily_health")
    assert hasattr(PaceModel, "easy_pace")
