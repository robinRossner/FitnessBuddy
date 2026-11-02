"""The engine config the tests use (`engine_config`, pinned in tests/support/config.py) is the
newest file: adding engine.vN+1.yaml fails here until the pin moves, so a bump is deliberate."""

from __future__ import annotations

from tests.support.config import ENGINE_CONFIG_PATH, engine_config_versions


def test_the_pinned_engine_config_is_the_newest(engine_config_path, engine_config):
    versions = engine_config_versions()
    assert engine_config_path == ENGINE_CONFIG_PATH
    assert engine_config_path.name == f"engine.v{max(versions)}.yaml", versions
    assert engine_config.version == f"v{max(versions)}"
