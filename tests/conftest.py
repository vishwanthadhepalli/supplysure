import os
import tempfile
from pathlib import Path

# Point the app's default engine at a throwaway file before anything imports app.db,
# so tests never create ./supplysure.db.
_TEST_DB = Path(tempfile.gettempdir()) / f"supplysure-test-{os.getpid()}.db"
os.environ["DATABASE_URL"] = f"sqlite:///{_TEST_DB}"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.db import create_schema, get_session, make_engine  # noqa: E402


@pytest.fixture
def engine(tmp_path):
    eng = make_engine(f"sqlite:///{tmp_path / 'test.db'}")
    create_schema(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def session(engine):
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as s:
        yield s


@pytest.fixture
def seeded_session(session):
    from scripts.seed import seed

    seed(session)
    return session


@pytest.fixture
def client():
    from app.main import app

    with TestClient(app) as c:
        yield c


@pytest.fixture
def api(seeded_session):
    """TestClient whose requests read the seeded per-test database."""
    from app.main import app

    app.dependency_overrides[get_session] = lambda: seeded_session
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def pytest_sessionfinish(session, exitstatus):
    _TEST_DB.unlink(missing_ok=True)
