"""Database engine, sessions and schema creation.

SQLite for dev and tests, Postgres via DATABASE_URL.
"""

import logging

from sqlalchemy import Engine, create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_database_url


class Base(DeclarativeBase):
    pass


def make_engine(url: str | None = None) -> Engine:
    url = url or get_database_url()
    kwargs: dict = {}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
    return create_engine(url, **kwargs)


engine = make_engine()
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


log = logging.getLogger("supplysure.db")


def create_schema(bind: Engine | None = None) -> list[str]:
    """Create missing tables, then add missing nullable columns to existing tables.

    create_all never alters an existing table, so a column added to a model later would break
    an older database (it did: D-59). Adding nullable columns is safe and automatic. Anything
    else (renames, type or NOT NULL changes) still needs a fresh database or a real migration.
    Returns the columns that were added.
    """
    from app import models  # noqa: F401  (registers every table on Base.metadata)

    eng = bind or engine
    Base.metadata.create_all(eng)
    added: list[str] = []
    insp = inspect(eng)
    with eng.begin() as conn:
        for table in Base.metadata.sorted_tables:
            existing = {c["name"] for c in insp.get_columns(table.name)}
            for col in table.columns:
                if col.name in existing or not col.nullable or col.primary_key:
                    continue
                ddl = col.type.compile(dialect=eng.dialect)
                conn.execute(text(f'ALTER TABLE "{table.name}" ADD COLUMN "{col.name}" {ddl}'))
                added.append(f"{table.name}.{col.name}")
    for name in added:
        log.warning("added missing column %s", name)
    return added


def get_session():
    """FastAPI dependency: yields a session and closes it afterwards."""
    session: Session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
