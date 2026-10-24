from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.orm import Session, sessionmaker

from app.db.session import make_engine
from app.importers.strong.importer import ImportOptions
from app.settings import BACKEND_DIR

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
def db(tmp_path: Path):
    url = f"sqlite:///{tmp_path / 'test.db'}"
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    cfg.attributes["url"] = url
    command.upgrade(cfg, "head")
    engine = make_engine(url)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    session: Session = factory()
    yield session
    session.close()
    engine.dispose()


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
        catalog_path=BACKEND_DIR / "data" / "catalog" / "exercises.yaml",
        engine_config_path=BACKEND_DIR / "config" / "engine.v1.yaml",
        tz="Europe/Vienna",
        apply=True,
    )
