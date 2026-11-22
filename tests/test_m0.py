from sqlalchemy import inspect

from app.db import Base
from app.models import Order, Supplier
from scripts.seed import seed

EXPECTED_TABLES = {
    "suppliers",
    "materials",
    "process_steps",
    "machines",
    "downtime",
    "production_settings",
    "assistant_inputs",
    "assistant_proposals",
    "orders",
    "order_materials",
    "messages",
    "promises",
    "documents",
    "risk_events",
    "graph_edges",
    "reminders",
    "action_log",
    "predictions",
}


def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_schema_creates_all_tables(engine):
    assert set(inspect(engine).get_table_names()) == EXPECTED_TABLES
    assert set(Base.metadata.tables) == EXPECTED_TABLES


def test_seed_creates_orders_and_suppliers(session):
    counts = seed(session)
    assert counts["orders"] == 3
    assert counts["suppliers"] == 3
    assert session.query(Order).count() == 3
    assert session.get(Supplier, "sup-ravi").single_source is True


def test_seed_is_idempotent(session):
    seed(session)
    counts = seed(session)
    assert counts["orders"] == 3
    assert counts["suppliers"] == 3
    assert counts["order_materials"] == 4


def test_schema_adds_missing_nullable_columns_to_an_old_database(tmp_path):
    """An older database must keep working after a nullable column is added to a model."""
    from sqlalchemy import text

    from app.db import create_schema, make_engine

    eng = make_engine(f"sqlite:///{tmp_path / 'old.db'}")
    with eng.begin() as conn:  # the assistant_inputs table as an earlier version created it
        conn.execute(text(
            "CREATE TABLE assistant_inputs (id VARCHAR PRIMARY KEY, created_at DATETIME NOT NULL, "
            "kind VARCHAR NOT NULL, language_code VARCHAR, original_text TEXT NOT NULL, "
            "english_text TEXT NOT NULL, reply TEXT)"
        ))  # fmt: skip
    added = create_schema(eng)
    assert "assistant_inputs.reply_local" in added
    assert "reply_local" in {c["name"] for c in inspect(eng).get_columns("assistant_inputs")}
    assert create_schema(eng) == []  # idempotent
