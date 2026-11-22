"""M5: read-only API. Numbers must match the golden planner file through the DB and HTTP layer."""

import json
from datetime import date, datetime
from pathlib import Path

import pytest

from app.models import Message, Promise
from scripts.sample_messages import replay

GOLDEN = json.loads(
    (Path(__file__).resolve().parents[1] / "data" / "sample" / "golden_planner.json").read_text(
        encoding="utf-8"
    )
)
FIELDS = ("dye_start", "dye_finish", "knit_start", "knit_finish", "pack_finish", "ready")


def _params(flags: dict) -> list[tuple[str, str]]:
    params = [
        ("second_shift", str(flags["second_shift"]).lower()),
        ("new_request_ahead", str(flags["new_request_ahead_of_SO_1190"]).lower()),
        ("yarn_delay_days", str(flags["yarn_delay_days"])),
    ]
    for d in flags["extra_downtime"]:
        params.append(("extra_down", f"{d['step']}:{d['from']}:{d['to']}:{d['machines_down']}"))
    return params


def _flat(order: dict) -> dict:
    steps = {s["id"]: s for s in order["steps"]}
    return {
        "dye_start": order["dye"]["start"],
        "dye_finish": order["dye"]["finish"],
        "knit_start": steps["knit"]["start"],
        "knit_finish": steps["knit"]["finish"],
        "pack_finish": steps["pack"]["finish"],
        "ready": order["ready"],
        "ship_by": order["ship_by"],
        "slack_days": order["slack_days"],
    }


@pytest.mark.parametrize("case", GOLDEN["cases"], ids=[c["name"] for c in GOLDEN["cases"]])
def test_plan_endpoint_matches_golden(api, case):
    body = api.get("/api/plan", params=_params(case["flags"])).json()
    got = {o["id"]: _flat(o) for o in body["orders"]}
    assert got == case["orders"]


def test_plan_matches_golden_after_sample_messages(api, seeded_session):
    replay(seeded_session)
    body = api.get("/api/plan").json()
    got = {o["id"]: _flat(o) for o in body["orders"]}
    assert got == GOLDEN["cases"][0]["orders"]
    so_1182 = next(o for o in body["orders"] if o["id"] == "SO-1182")
    # m1's promise (bales on the 22nd) replaces the planned Jun 25 yarn date, see D-33.
    assert so_1182["yarn"]["source"] == "promise"
    assert so_1182["yarn"]["date"] == "2027-06-22"
    assert so_1182["yarn"]["planned"] == "2027-06-25"


def test_later_yarn_promise_moves_the_plan(api, seeded_session):
    seeded_session.add(
        Message(
            id="msg-test-late",
            source="test",
            source_message_id="late",
            supplier_id="sup-ravi",
            received_at=datetime(2027, 6, 20, 9, 0),
            kind="text",
            raw_text="Yarn on the 29th",
        )
    )
    seeded_session.add(
        Promise(
            id="msg-test-late-p0",
            message_id="msg-test-late",
            supplier_id="sup-ravi",
            order_id="SO-1182",
            material_id="cotton_yarn_kg",
            promised_date=date(2027, 6, 29),
            status="open",
            quote="Yarn on the 29th",
            quote_verified=True,
            created_at=datetime(2027, 6, 20, 9, 0),
        )
    )
    seeded_session.commit()
    body = api.get("/api/plan").json()
    so_1182 = next(o for o in body["orders"] if o["id"] == "SO-1182")
    # Jun 29 + 1 misses the Jun 28 dye slot: next slot Jul 7 (same as golden yarn_late_4).
    assert so_1182["dye"]["start"] == "2027-07-07"
    assert so_1182["ready"] == "2027-07-31"
    assert so_1182["baseline"] == {"ready": "2027-07-31", "slack_days": -11}
    without = api.get("/api/plan", params={"use_promises": "false"}).json()
    assert next(o for o in without["orders"] if o["id"] == "SO-1182")["ready"] == "2027-07-20"


def test_scenarios_match_golden(api):
    body = api.get("/api/plan/scenarios").json()
    assert len(body["scenarios"]) == len(GOLDEN["cases"])
    for scenario, case in zip(body["scenarios"], GOLDEN["cases"], strict=True):
        for oid, want in case["orders"].items():
            got = scenario["orders"][oid]
            assert (got["ready"], got["slack_days"]) == (want["ready"], want["slack_days"]), (
                scenario["key"],
                oid,
            )


def test_chart_capacity_reflects_downtime_and_shift(api):
    chart = api.get("/api/plan", params={"second_shift": "true"}).json()["chart"]
    knit = next(s for s in chart["steps"] if s["id"] == "knit")
    start = date.fromisoformat(chart["start"])
    jul_20 = (date(2027, 7, 20) - start).days
    assert knit["nominal"] == 6300
    assert knit["capacity"][jul_20] == 5250  # 10 machines x 350 x 1.5 during maintenance


def test_order_detail_has_golden_netting(api):
    body = api.get("/api/orders/SO-1182").json()
    got = {m["id"]: (m["need"], m["buy"]) for m in body["materials"]}
    want = {i["item"]: (i["need"], i["buy"]) for i in GOLDEN["materials_netting"]["items"]}
    assert got == want
    assert body["order"]["verdict"]["level"] == "warn"  # slack 0: feasible, tight
    assert api.get("/api/orders/SO-9999").status_code == 404


def test_ledger_after_sample_messages(api, seeded_session):
    replay(seeded_session)
    body = api.get("/api/promises").json()
    assert body["total"] == 3
    assert body["counts"]["open"] == 2
    assert body["counts"]["needs_clarification"] == 1
    for p in body["promises"]:
        assert p["quote_verified"] is True
        assert p["quote"] in p["message"]["text"]
    pending = api.get("/api/promises", params={"status": "needs_clarification"}).json()
    assert [p["material"]["id"] for p in pending["promises"]] == ["cartons_pcs"]
    assert pending["promises"][0]["clarification_question"]
    assert api.get("/api/promises", params={"status": "delivered"}).status_code == 422


def test_activity_shows_blocked_hostile_message(api, seeded_session):
    replay(seeded_session)
    actions = [a["action"] for a in api.get("/api/activity").json()["actions"]]
    assert "suspicious_message" in actions
    assert "duplicate_message_ignored" in actions


def test_graph_and_traces(api, seeded_session):
    replay(seeded_session)
    graph = api.get("/api/graph").json()
    ids = {n["id"] for n in graph["nodes"]}
    assert {
        "supplier:sup-ravi",
        "material:cotton_yarn_kg",
        "step:knit",
        "machine:machine-knit",
        "order:SO-1195",
    } <= ids
    assert {"src": "machine:machine-knit", "dst": "step:knit", "rel": "works on"} in graph["edges"]
    assert {"src": "step:pack", "dst": "order:SO-1182", "rel": "makes"} in graph["edges"]
    assert {"src": "supplier:sup-ravi", "dst": "material:cotton_yarn_kg", "rel": "supplies"} in (
        graph["edges"]
    )
    assert set(graph["single_source"]) == {"material:cotton_yarn_kg", "material:elastane_kg"}
    by_id = {n["id"]: n for n in graph["nodes"]}
    assert by_id["order:SO-1195"]["status"] == "risk"  # -6 days in the base plan
    assert by_id["supplier:sup-chennai"]["status"] == "warn"  # needs clarification

    down = api.get("/api/graph/trace", params={"node": "supplier:sup-ravi"}).json()
    assert down["orders"] == ["order:SO-1182"]
    maint = next(n for n in ids if n.startswith("event:downtime-knit"))
    hit = api.get("/api/graph/trace", params={"node": maint}).json()
    assert set(hit["orders"]) == {"order:SO-1182", "order:SO-1190", "order:SO-1195"}
    up = api.get(
        "/api/graph/trace", params={"node": "order:SO-1182", "direction": "up"}
    ).json()
    assert "supplier:sup-sriprint" in up["reached"]
    assert api.get("/api/graph/trace", params={"node": "order:nope"}).status_code == 404


def test_what_if_flags_are_validated(api):
    assert api.get("/api/plan", params={"extra_down": "knit:bad"}).status_code == 422
    bad_step = {"extra_down": "weave:2027-07-03:2027-07-10:2"}
    assert api.get("/api/plan", params=bad_step).status_code == 422
    assert api.get("/api/plan", params={"yarn_delay_days": "-1"}).status_code == 422


# Owner-editable factory setup (D-45), the owner's own orders (D-60) and the owner's
# assistant (D-50) may write. Supplier data may not.
WRITABLE_PREFIXES = (
    "/api/machines", "/api/downtime", "/api/process", "/api/supplygpt", "/api/orders",
)  # fmt: skip
WRITE_METHODS = {"post", "put", "patch", "delete"}


def _api_methods():
    """(path, method) for every API operation. Uses the OpenAPI document, because newer FastAPI
    versions wrap included routers and app.routes no longer lists their routes."""
    from app.main import app

    paths = app.openapi()["paths"]
    return [(path, m) for path, ops in paths.items() for m in ops if path.startswith("/api")]


def test_supplier_data_is_read_only_from_the_web():
    """Promises, messages, reminders and the action log can only be written by intake, never
    through a web endpoint. Only factory setup and SupplyGPT endpoints may write."""
    ops = _api_methods()
    assert len(ops) > 20  # guard: the check must actually see the routes
    writes = [(p, m) for p, m in ops if m in WRITE_METHODS]
    assert writes, "expected some setup write endpoints"
    offenders = [(p, m) for p, m in writes if not p.startswith(WRITABLE_PREFIXES)]
    assert offenders == []
    for path in ("/api/promises", "/api/plan", "/api/activity", "/api/graph"):
        assert {m for p, m in ops if p == path} == {"get"}, path


def test_web_app_is_served(api):
    page = api.get("/")
    assert page.status_code == 200
    assert "SupplySure" in page.text
    assert api.get("/app.js").status_code == 200
