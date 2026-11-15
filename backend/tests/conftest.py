"""Test fixtures: a real PostgreSQL test database migrated with Alembic; tables truncated per test."""
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import make_engine
from app.models import Base

BACKEND_DIR = Path(__file__).resolve().parents[1]


def alembic_config(db_name: str) -> Config:
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    cfg.cmd_opts = type("Opts", (), {"x": [f"db={db_name}"]})()
    return cfg


@pytest.fixture(scope="session")
def test_db_name() -> str:
    return get_settings().test_db_name


@pytest.fixture(scope="session")
def engine(test_db_name):
    eng = make_engine(test_db_name)
    command.upgrade(alembic_config(test_db_name), "head")
    yield eng
    eng.dispose()


@pytest.fixture()
def session(engine):
    """Clean tables before each test (TRUNCATE bypasses the append-only row triggers)."""
    tables = ", ".join(
        f'"{t.schema}"."{t.name}"' if t.schema else f'"{t.name}"' for t in Base.metadata.sorted_tables)
    with engine.begin() as conn:
        conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
    with Session(engine) as s:
        yield s
        s.rollback()


@pytest.fixture()
def client(engine):
    """TestClient whose database dependency points at the test database."""
    from fastapi.testclient import TestClient

    from app.api.deps import get_db
    from app.main import app
    from app.repositories.bulk_repository import clear_cache

    def _db():
        with Session(engine) as s:
            yield s

    app.dependency_overrides[get_db] = _db
    clear_cache()
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c
    app.dependency_overrides.clear()
    clear_cache()
