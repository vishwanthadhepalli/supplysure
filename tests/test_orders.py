"""Creating and updating buyer orders: API and SupplyGPT (D-60)."""

import pytest
from sqlalchemy import select

from app.models import ActionLog, Order
from app.providers.sarvam import FakeSarvam
from app.supplygpt.api import get_client

TODAY = "2027-06-14"  # Monday


def act(type_, quote, **f):
    keys = ("name", "step_id", "machine_id", "unit", "count", "rate_per_machine_day",
            "machines_down", "from_text", "to_text", "duration_days", "planned", "reason",
            "supplier_id", "material_id", "quantity", "order_id", "buyer", "pairs",
            "ship_by_text", "yarn_text", "status", "grams_per_pair")  # fmt: skip
    return {**{k: None for k in keys}, "type": type_, "quote": quote, **f}


NEW = "Create an order for Delhi Mart: 12,000 pairs, ship by 20 August, yarn arrives on 2 July."
NEW_NO_YARN = "New enquiry from Kerala Sports, 8000 pairs to ship by 30 August."
YARN = "For SO-1196 the yarn will come on the 10th."
NUMBERED = "Book order SO-1300 for Pune Traders, 5000 pairs, ship by 25 August."


@pytest.fixture
def gpt(api, monkeypatch):
    from app.main import app

    monkeypatch.setenv("SUPPLYSURE_TODAY", TODAY)
    fake = FakeSarvam(chats={
        "Delhi Mart": {"reply": "", "actions": [act(
            "create_order", NEW.rstrip("."), buyer="Delhi Mart", pairs=12000,
            ship_by_text="20 August", yarn_text="2 July", status="booked")]},
        "Kerala Sports": {"reply": "", "actions": [act(
            "create_order", NEW_NO_YARN.rstrip("."), buyer="Kerala Sports", pairs=8000,
            ship_by_text="30 August", status="tentative")]},
        "SO-1196 the yarn": {"reply": "", "actions": [act(
            "update_order", "For SO-1196 the yarn will come on the 10th", order_id="SO-1196",
            yarn_text="the 10th")]},
        "Pune Traders": {"reply": "", "actions": [act(
            "create_order", NUMBERED.rstrip("."), order_id="SO-1300", buyer="Pune Traders",
            pairs=5000, ship_by_text="25 August")]},
        "made up": {"reply": "", "actions": [act(
            "create_order", "made up order for Goa Shop, 3000 pairs, ship by 1 September",
            order_id="SO-7777", buyer="Goa Shop", pairs=4000, ship_by_text="1 September")]},
    })  # fmt: skip
    app.dependency_overrides[get_client] = lambda: fake
    yield api
    app.dependency_overrides.pop(get_client, None)


def _say(gpt, text):
    r = gpt.post("/api/supplygpt/text", json={"text": text})
    assert r.status_code == 201, r.text
    return r.json()["proposals"]


def _apply(gpt, p):
    r = gpt.post(f"/api/supplygpt/proposals/{p['id']}/apply")
    assert r.status_code == 200, r.text
    return r.json()


def test_api_create_and_update_order(api, seeded_session):
    r = api.post("/api/orders", json={"buyer": "Test Buyer", "pairs": 1000,
                                      "ship_by": "2027-09-01", "yarn_arrival": "2027-07-20"})
    assert r.status_code == 201 and r.json()["id"] == "SO-1196"  # next after SO-1195
    row = seeded_session.get(Order, "SO-1196")
    assert row.status == "booked" and row.grams_per_pair == 90  # copied from the latest order
    assert api.post("/api/orders", json={"id": "SO-1196", "buyer": "x", "pairs": 1,
                                         "ship_by": "2027-09-01"}).status_code == 409
    assert api.post("/api/orders", json={"buyer": "x", "pairs": 0,
                                         "ship_by": "2027-09-01"}).status_code == 422
    assert api.patch("/api/orders/SO-1196", json={"status": "tentative"}).status_code == 200
    assert api.patch("/api/orders/SO-1196", json={}).status_code == 422
    assert api.patch("/api/orders/NOPE", json={"pairs": 5}).status_code == 404
    plan = {o["id"]: o for o in api.get("/api/plan").json()["orders"]}
    assert "SO-1196" in plan  # it has a yarn date, so it is planned
    actions = [a.action for a in seeded_session.scalars(select(ActionLog))]
    assert "order_created" in actions and "order_updated" in actions


def test_supplygpt_creates_a_planned_order(gpt, seeded_session):
    (p,) = _say(gpt, NEW)
    assert p["status"] == "pending"
    assert p["payload"]["ship_by"] == "2027-08-20" and p["payload"]["yarn_arrival"] == "2027-07-02"
    assert "Create order (next number, now SO-1196) for Delhi Mart: 12,000 pairs" in p["summary"]
    assert "ship by Aug 20" in p["summary"]
    assert seeded_session.get(Order, "SO-1196") is None  # nothing until approved
    done = _apply(gpt, p)
    assert done["result_ref"] == "SO-1196"
    o = next(o for o in gpt.get("/api/plan").json()["orders"] if o["id"] == "SO-1196")
    assert o["ship_by"] == "2027-08-20" and o["pairs"] == 12000 and o["ready"]


def test_order_without_yarn_date_waits_then_gets_planned(gpt, seeded_session):
    (p,) = _say(gpt, NEW_NO_YARN)
    assert "tentative" in p["summary"] and "not planned" in p["summary"]
    _apply(gpt, p)
    plan = gpt.get("/api/plan").json()
    assert "SO-1196" in plan["unplanned"]
    (u,) = _say(gpt, YARN)
    assert u["payload"] == {"order_id": "SO-1196", "yarn_arrival": "2027-07-10"}
    assert "yarn arrives Jul 10 (was none)" in u["summary"]
    _apply(gpt, u)
    planned = {o["id"] for o in gpt.get("/api/plan").json()["orders"]}
    assert "SO-1196" in planned


def test_order_number_is_used_only_if_the_owner_said_it(gpt):
    (p,) = _say(gpt, NUMBERED)
    assert p["payload"]["id"] == "SO-1300"
    (bad,) = _say(gpt, "made up order for Goa Shop, 3000 pairs, ship by 1 September")
    assert bad["status"] == "invalid"  # 4000 pairs is not what was said
