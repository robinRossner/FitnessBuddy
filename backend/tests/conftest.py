from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.orm import Session, sessionmaker

from apps.fitness.db.session import make_engine
from apps.fitness.importers.strong.importer import ImportOptions
from apps.fitness.settings import FITNESS_DIR

# advisor fixtures (world, fake, client, ...); receipts fixtures (rfactory, rclient, FakeProvider)
pytest_plugins = ("tests.advisor_helpers", "tests.receipts_helpers")

FIXTURES = Path(__file__).parent / "fixtures"

TEST_MAPPING = """\
strong_name,canonical_exercise,variant_name,equipment_type,load_convention,status,notes
Bench Press (Dumbbell),bench_press,Bench Press (Dumbbell),dumbbell,per_hand,auto,
Bench Press (Barbell),bench_press,Bench Press (Barbell),barbell,total,confirmed,
Pull Up,pull_up,Pull Up,bodyweight,total,confirmed,
Plank,plank,Plank,bodyweight,total,auto,
L-Sit,l_sit,L-Sit,bodyweight,total,auto,
Squat (Barbell),squat,Squat (Barbell),barbell,total,confirmed,
Lat Pulldown (Cable),lat_pulldown,Lat Pulldown (Cable),cable,total,auto,
Running,cardio,Running,other,total,auto,
Mystery Exercise,,,,,unmapped,
"""


@pytest.fixture
def session_factory(tmp_path: Path):
    """sessionmaker bound to a fresh, fully migrated SQLite database."""
    url = f"sqlite:///{tmp_path / 'test.db'}"
    cfg = Config(str(FITNESS_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(FITNESS_DIR / "alembic"))
    cfg.attributes["url"] = url
    command.upgrade(cfg, "head")
    engine = make_engine(url)
    yield sessionmaker(bind=engine, expire_on_commit=False)
    engine.dispose()


@pytest.fixture
def db(session_factory):
    session: Session = session_factory()
    yield session
    session.close()


@pytest.fixture
def mapping_path(tmp_path: Path) -> Path:
    p = tmp_path / "strong_mapping.csv"
    p.write_text(TEST_MAPPING, encoding="utf-8")
    return p


@pytest.fixture
def imports_dir(tmp_path: Path) -> Path:
    d = tmp_path / "imports" / "strong"
    d.mkdir(parents=True)
    shutil.copy(FIXTURES / "strong_basic.csv", d / "export_a.csv")
    return d


@pytest.fixture
def options(imports_dir: Path, mapping_path: Path) -> ImportOptions:
    return ImportOptions(
        paths=[imports_dir],
        mapping_path=mapping_path,
        catalog_path=FITNESS_DIR / "data" / "catalog" / "exercises.yaml",
        engine_config_path=FITNESS_DIR / "config" / "engine.v2.yaml",
        tz="Europe/Vienna",
        apply=True,
    )


@pytest.fixture(autouse=True)
def _live_program_in_tmp(tmp_path: Path, monkeypatch):
    """The live program file (next to the DB by default) never points at the real backend/var."""
    monkeypatch.setenv("PROGRAM_LIVE_PATH", str(tmp_path / "live" / "program.yaml"))


@pytest.fixture(autouse=True)
def _auth_throttle_off(monkeypatch):
    """Tests send many bad tokens from one fake client; the throttle is tested on its own."""
    from core.auth import get_throttle, get_trusted_proxies
    from core.settings import get_core_settings

    monkeypatch.setenv("AUTH_MAX_FAILURES", "1000000")
    for cached in (get_core_settings, get_throttle, get_trusted_proxies):
        cached.cache_clear()
    yield
    for cached in (get_core_settings, get_throttle, get_trusted_proxies):
        cached.cache_clear()
