"""The engine config file the tests pin (`engine_config` fixture in tests/conftest.py)."""

from __future__ import annotations

import re

from apps.fitness.settings import FITNESS_DIR

# The newest engine.vN.yaml; tests/engine/test_config_pin.py fails when a newer file appears.
# The shared world seeds its own database from an older file, see support/world.py.
ENGINE_CONFIG_PATH = FITNESS_DIR / "config" / "engine.v6.yaml"


def engine_config_versions() -> list[int]:
    """The N of every engine.vN.yaml in the app's config directory."""
    names = (p.name for p in ENGINE_CONFIG_PATH.parent.iterdir())
    return sorted(int(m.group(1)) for n in names if (m := re.fullmatch(r"engine\.v(\d+)\.yaml", n)))
