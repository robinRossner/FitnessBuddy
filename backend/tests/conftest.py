"""Root fixtures shared by every test group: the migrated database (one template per session,
copied per test), the shared synthetic `world` with its API `client`, the pinned engine config,
the Strong import options, and the safety autouse fixture. Group conftests (tests/<group>/) add
that group's fixtures and the isolation autouse fixtures it needs (`auth_throttle_off`,
`clean_settings`, defined here)."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from apps.fitness.advisor import claude
from apps.fitness.advisor import validator as V
from apps.fitness.analytics import cache
from apps.fitness.api.deps import get_db
from apps.fitness.db import session as dbsession
from apps.fitness.db.session import make_engine
from apps.fitness.engine.core.config import EngineConfig, load_engine_config
from apps.fitness.engine.core.program import load_program
from apps.fitness.engine.store import config_service as cs
from apps.fitness.importers.strong.importer import ImportOptions
from apps.fitness.settings import FITNESS_DIR, Settings, get_settings
from core.auth import get_api_token
from core.settings import get_core_settings
from server import app
from tests.support.config import ENGINE_CONFIG_PATH
from tests.support.db import migrate_template
from tests.support.fakes_anthropic import FakeClient
from tests.support.world import AUTH, PROGRAM_YAML, TOKEN, World, build_world, configure

TESTS_DIR = Path(__file__).parent
FIXTURES = TESTS_DIR / "fixtures"
GROUPS = (
    "engine",
    "advisor",
    "garmin",
    "importers",
    "api",
    "gyms",
    "sync",
    "analytics",
    "withings",
)
DB_FIXTURES = frozenset({"db", "session_factory", "client", "world"})

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


# --------------------------------------------------------------------------- markers


def pytest_collection_modifyitems(config, items):
    """Mark every test with its folder's group (`-m engine`) and with `db` when it needs a
    migrated database, directly or through another fixture (`-m "not db"` is the fast set)."""
    for item in items:
        rel = Path(item.path).relative_to(TESTS_DIR).parts
        if len(rel) > 1 and rel[0] in GROUPS:
            item.add_marker(getattr(pytest.mark, rel[0]))
        if DB_FIXTURES & set(item._fixtureinfo.names_closure):
            item.add_marker(pytest.mark.db)


# --------------------------------------------------------------------------- database


@pytest.fixture(scope="session")
def template_db(tmp_path_factory) -> Path:
    """A database migrated to head once per session (per xdist worker); tests get a copy."""
    return migrate_template(tmp_path_factory.mktemp("template") / "template.db")


@pytest.fixture
def session_factory(tmp_path: Path, template_db: Path):
    """sessionmaker bound to a fresh, fully migrated SQLite database (a copy of the template)."""
    path = tmp_path / "test.db"
    shutil.copy(template_db, path)
    engine = make_engine(f"sqlite:///{path}")
    yield sessionmaker(bind=engine, expire_on_commit=False)
    engine.dispose()


@pytest.fixture
def db(session_factory):
    session: Session = session_factory()
    yield session
    session.close()


@pytest.fixture
def pin_session_factory(session_factory, monkeypatch):
    """Make `get_session_factory()` (background tasks, jobs) use the test database."""
    monkeypatch.setattr(dbsession, "_factory", session_factory)
    return session_factory


# --------------------------------------------------------------------------- engine config


@pytest.fixture(scope="session")
def engine_config_path() -> Path:
    return ENGINE_CONFIG_PATH


@pytest.fixture
def engine_config(engine_config_path: Path) -> EngineConfig:
    return load_engine_config(engine_config_path)


# --------------------------------------------------------------------------- Strong import


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
def options(imports_dir: Path, mapping_path: Path, engine_config_path: Path) -> ImportOptions:
    return ImportOptions(
        paths=[imports_dir],
        mapping_path=mapping_path,
        catalog_path=FITNESS_DIR / "data" / "catalog" / "exercises.yaml",
        engine_config_path=engine_config_path,
        tz="Europe/Vienna",
        apply=True,
    )


# --------------------------------------------------------------------------- the shared world


@pytest.fixture
def fake(monkeypatch) -> FakeClient:
    """A FakeClient wired in as `claude.make_client` (replace `fake.output` etc. in the test)."""
    client = FakeClient()
    monkeypatch.setattr(claude, "make_client", lambda settings: client)
    return client


@pytest.fixture
def env(monkeypatch, tmp_path):
    """Settings for the world: program file, fake API key, API token (caches cleared after)."""
    yield configure(monkeypatch, tmp_path)
    get_settings.cache_clear()
    get_core_settings.cache_clear()


@pytest.fixture
def world(db, env: Settings, monkeypatch) -> World:
    return build_world(db, monkeypatch)


@pytest.fixture
def no_rerun(monkeypatch) -> list[str]:
    """Replace the engine re-run after an applied change by a recorder."""
    calls: list[str] = []
    monkeypatch.setattr(
        "apps.fitness.advisor._common.rerun_engine_after_change", lambda: calls.append("rerun")
    )
    return calls


@pytest.fixture
def client(world, session_factory, pin_session_factory, no_rerun, fake):
    """The real app on the world's database, authenticated with the test token."""

    def _db():
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_api_token] = lambda: TOKEN
    yield TestClient(app, headers=AUTH)
    app.dependency_overrides.clear()


@pytest.fixture
def ctx(db, world, tmp_path):
    """Advisor validator context on the world (active config + the test program)."""
    _cfg, row = cs.load_active(db)
    program = tmp_path / "p.yaml"
    program.write_text(PROGRAM_YAML)
    return V.build_context(db, row.content, program=load_program(program), settings=get_settings())


# --------------------------------------------------------------------------- isolation


@pytest.fixture(autouse=True)
def _live_program_in_tmp(tmp_path: Path, monkeypatch):
    """Nothing reaches the real backend/var: the live program file and the default database
    (anything not pinned to the test DB, e.g. `get_session_factory()`) point into tmp_path, and
    no session factory cached by an earlier test survives. Settings are re-read so the variables
    take effect even if an earlier test cached them."""
    monkeypatch.setenv("PROGRAM_LIVE_PATH", str(tmp_path / "live" / "program.yaml"))
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'unpinned.db'}")
    monkeypatch.setattr(dbsession, "_engine", None)
    monkeypatch.setattr(dbsession, "_factory", None)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def auth_throttle_off(monkeypatch):
    """Tests send many bad tokens from one fake client; the throttle is tested on its own.
    Used autouse by the groups whose tests send bad tokens (see their conftest.py)."""
    from core.auth import get_throttle, get_trusted_proxies

    monkeypatch.setenv("AUTH_MAX_FAILURES", "1000000")
    for cached in (get_core_settings, get_throttle, get_trusted_proxies):
        cached.cache_clear()
    yield
    for cached in (get_core_settings, get_throttle, get_trusted_proxies):
        cached.cache_clear()


@pytest.fixture
def clean_settings():
    """Fresh settings caches and stats cache per test; no dependency override survives it."""
    get_settings.cache_clear()
    get_core_settings.cache_clear()
    cache.invalidate()
    yield
    get_settings.cache_clear()
    get_core_settings.cache_clear()
    app.dependency_overrides.clear()
