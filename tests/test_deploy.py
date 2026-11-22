"""Hosted-deployment features: password protection and seeding an empty database (D-63)."""

import base64

from fastapi.testclient import TestClient


def _auth(user, pw):
    return {"Authorization": "Basic " + base64.b64encode(f"{user}:{pw}".encode()).decode()}


def test_no_password_means_open(monkeypatch, client):
    monkeypatch.delenv("APP_PASSWORD", raising=False)
    assert client.get("/health").status_code == 200
    assert client.get("/").status_code == 200


def test_password_protects_everything_but_health(monkeypatch):
    from app.main import app

    monkeypatch.setenv("APP_PASSWORD", "s3cret-pass")
    monkeypatch.delenv("APP_USERNAME", raising=False)
    with TestClient(app) as c:
        assert c.get("/health").status_code == 200  # host health check stays open
        for path in ("/", "/app.js", "/api/plan", "/api/supplygpt/status"):
            r = c.get(path)
            assert r.status_code == 401, path
            assert r.headers["www-authenticate"].startswith("Basic")
        assert c.get("/api/plan", headers=_auth("supplysure", "wrong")).status_code == 401
        assert c.get("/api/plan", headers=_auth("someone", "s3cret-pass")).status_code == 401
        assert c.get("/", headers=_auth("supplysure", "s3cret-pass")).status_code == 200
        assert c.post("/api/orders", json={}, headers=_auth("x", "y")).status_code == 401
        bad = {"Authorization": "Basic !!!not-base64"}
        assert c.get("/api/plan", headers=bad).status_code == 401


def test_seed_if_empty_loads_once(monkeypatch, tmp_path):
    from sqlalchemy.orm import sessionmaker

    import app.main as main
    from app.db import create_schema, make_engine
    from app.models import Order, Promise

    eng = make_engine(f"sqlite:///{tmp_path / 'fresh.db'}")
    create_schema(eng)
    monkeypatch.setattr(main, "SessionLocal", sessionmaker(bind=eng, expire_on_commit=False))
    assert main.seed_if_empty() is True
    with main.SessionLocal() as s:
        assert s.query(Order).count() == 3
        assert s.query(Promise).count() == 3  # sample messages replayed
    assert main.seed_if_empty() is False  # never twice, never over real data
