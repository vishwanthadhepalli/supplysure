"""Machines and Production plan modules: owner-editable factory setup (D-43 to D-46)."""

from sqlalchemy import select

from app.models import ActionLog, Downtime

KNIT = "machine-knit"


def _plan(api, **params):
    return {o["id"]: o for o in api.get("/api/plan", params=params).json()["orders"]}


def _actions(session, name):
    return session.scalars(select(ActionLog).where(ActionLog.action == name)).all()


def test_machines_show_orders_load_and_downtime(api):
    body = api.get("/api/machines").json()
    assert body["bottleneck"] == "knit"
    machines = {m["id"]: m for m in body["machines"]}
    assert len(machines) == 5
    knit = machines[KNIT]
    assert knit["name"] == "Knitting machines"
    assert knit["nominal"] == 4200
    assert [o["id"] for o in knit["orders"]] == ["SO-1182", "SO-1190", "SO-1195"]
    assert sum(o["pairs"] for o in knit["orders"]) == 100_000  # every pair is knitted once
    assert 0 < knit["utilisation"] <= 1
    (maint,) = knit["downtime"]
    assert maint["planned"] is True
    assert maint["lost"] == 2 * 350 * 11  # 2 machines x 350 a day x Jul 15-25
    assert maint["affected_orders"] == ["SO-1190", "SO-1195"]
    # Daily rows cover the plan horizon and never exceed capacity.
    assert all(d["used"] <= d["capacity"] + 1e-6 for d in knit["daily"])


def test_adding_a_machine_group_speeds_up_the_plan_and_is_logged(api, seeded_session):
    before = _plan(api)["SO-1195"]
    r = api.post(
        "/api/machines",
        json={"name": "New knitting machines", "step": "knit", "count": 4,
              "rate_per_machine_day": 350},
    )  # fmt: skip
    assert r.status_code == 201
    after = _plan(api)["SO-1195"]
    assert after["ready"] < before["ready"]
    assert after["slack_days"] > before["slack_days"]
    assert _actions(seeded_session, "machine_added")[0].actor == "owner"
    knit_step = next(
        s for s in api.get("/api/production-plan").json()["steps"] if s["id"] == "knit"
    )
    assert knit_step["nominal"] == 16 * 350
    assert len(knit_step["machines"]) == 2


def test_probable_breakdown_pushes_the_plan_out(api, seeded_session):
    before = _plan(api)["SO-1182"]
    r = api.post(
        "/api/downtime",
        json={"machine_id": KNIT, "from_date": "2027-07-05", "to_date": "2027-07-08",
              "machines_down": 3, "planned": False, "reason": "Motor overheating"},
    )  # fmt: skip
    assert r.status_code == 201
    after = _plan(api)["SO-1182"]
    assert after["ready"] > before["ready"]
    knit = api.get(f"/api/machines/{KNIT}").json()["machine"]
    probable = [d for d in knit["downtime"] if not d["planned"]]
    assert probable[0]["reason"] == "Motor overheating"
    assert "SO-1182" in probable[0]["affected_orders"]
    assert api.delete(f"/api/downtime/{probable[0]['id']}").status_code == 200
    assert _plan(api)["SO-1182"]["ready"] == before["ready"]
    assert len(_actions(seeded_session, "downtime_deleted")) == 1


def test_downtime_is_validated(api):
    ok = {"machine_id": KNIT, "from_date": "2027-07-05", "to_date": "2027-07-08",
          "machines_down": 2}  # fmt: skip
    assert api.post("/api/downtime", json={**ok, "machines_down": 13}).status_code == 422
    assert api.post("/api/downtime", json={**ok, "to_date": "2027-07-01"}).status_code == 422
    assert api.post("/api/downtime", json={**ok, "machine_id": "nope"}).status_code == 404


def test_update_and_delete_machine(api, seeded_session):
    body = {"name": "Knitting machines", "step": "knit", "count": 10, "rate_per_machine_day": 350}
    assert api.put(f"/api/machines/{KNIT}", json=body).status_code == 200
    assert api.get(f"/api/machines/{KNIT}").json()["machine"]["nominal"] == 3500
    assert api.put(f"/api/machines/{KNIT}", json={**body, "step": "nope"}).status_code == 404
    assert api.put(f"/api/machines/{KNIT}", json={**body, "count": 0}).status_code == 422

    assert api.delete(f"/api/machines/{KNIT}").status_code == 200
    assert seeded_session.scalars(select(Downtime).where(Downtime.machine_id == KNIT)).all() == []
    plan = api.get("/api/plan").json()
    assert plan["orders"] == []
    assert any("Knitting has no working machines" in p for p in plan["problems"])


def test_route_editing(api, seeded_session):
    r = api.post("/api/process/steps", json={"name": "Ironing", "unit": "tables"})
    assert r.status_code == 201
    step_id = r.json()["id"]
    route = [s["id"] for s in api.get("/api/production-plan").json()["steps"]]
    assert route == ["knit", "link", "board", "pair", "pack", step_id]

    # A step with no machines blocks the plan with a clear problem, not a crash.
    plan = api.get("/api/plan").json()
    assert plan["orders"] == [] and plan["unplanned"]
    assert any("Ironing" in p for p in plan["problems"])

    api.post(
        "/api/machines",
        json={"name": "Ironing tables", "step": step_id, "count": 3, "rate_per_machine_day": 4000},
    )
    plan = _plan(api)
    assert [s["id"] for s in plan["SO-1182"]["steps"]][-1] == step_id

    moved = api.post(f"/api/process/steps/{step_id}/move", json={"direction": "up"}).json()
    assert moved["route"][-2:] == [step_id, "pack"]
    sideways = api.post(f"/api/process/steps/{step_id}/move", json={"direction": "sideways"})
    assert sideways.status_code == 422

    assert api.delete(f"/api/process/steps/{step_id}").status_code == 409  # machines assigned
    machine_id = next(
        m["id"] for m in api.get("/api/machines").json()["machines"] if m["step"]["id"] == step_id
    )
    api.delete(f"/api/machines/{machine_id}")
    assert api.delete(f"/api/process/steps/{step_id}").status_code == 200
    steps = api.get("/api/production-plan").json()["steps"]
    assert [s["sequence"] for s in steps] == [1, 2, 3, 4, 5]
    assert api.put("/api/process/steps/knit", json={"name": "Knitting floor"}).status_code == 200
    assert _plan(api)["SO-1182"]["steps"][0]["name"] == "Knitting floor"
    for action in ("step_added", "step_moved", "step_deleted", "step_updated"):
        assert _actions(seeded_session, action), action


def test_settings_change_dispatch_and_validation(api):
    before = _plan(api)
    settings = api.get("/api/production-plan").json()["settings"]
    r = api.put("/api/process/settings", json={**settings, "dispatch_days": 3})
    assert r.status_code == 200
    after = _plan(api)
    for oid in before:
        assert after[oid]["slack_days"] == before[oid]["slack_days"] - 1
    bad = {**settings, "second_shift_multiplier": 9}
    assert api.put("/api/process/settings", json=bad).status_code == 422


def test_production_plan_overview(api):
    body = api.get("/api/production-plan").json()
    steps = {s["id"]: s for s in body["steps"]}
    assert [s["id"] for s in body["steps"]] == ["knit", "link", "board", "pair", "pack"]
    assert steps["knit"]["bottleneck"] and steps["knit"]["nominal"] == 4200
    assert steps["knit"]["second_shift"] and not steps["pack"]["second_shift"]
    assert steps["pack"]["nominal"] == 6500
    assert body["settings"]["dye_days"] == 5
    assert body["problems"] == []


def test_brain_links_downtime_to_machine_and_reaches_orders(api):
    graph = api.get("/api/graph").json()
    edge = next(e for e in graph["edges"] if e["src"].startswith("event:downtime-knit"))
    assert edge["dst"] == f"machine:{KNIT}"
    hit = api.get("/api/graph/trace", params={"node": edge["src"]}).json()
    assert set(hit["orders"]) == {"order:SO-1182", "order:SO-1190", "order:SO-1195"}
    knit = next(n for n in graph["nodes"] if n["id"] == f"machine:{KNIT}")
    assert knit["attrs"]["Orders"] == "SO-1182, SO-1190, SO-1195"
    assert "busy" in knit["sub"]
