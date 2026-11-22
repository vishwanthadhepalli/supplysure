"""Planner generalisation (D-42): any route, several machine groups per step, machine-level
downtime, per-step second shift. The golden file still pins the sample behaviour."""

import json
from dataclasses import replace
from datetime import date
from pathlib import Path

from app.planner import (
    Downtime,
    Flags,
    MachineGroup,
    Order,
    Process,
    StepSpec,
    capacity,
    compute_plan,
    split_by_group,
)

SAMPLE = Path(__file__).resolve().parents[1] / "data" / "sample"
PROCESS = Process.from_dict(json.loads((SAMPLE / "process.json").read_text(encoding="utf-8")))
GOLDEN = json.loads((SAMPLE / "golden_planner.json").read_text(encoding="utf-8"))
ORDERS = [
    Order(o["id"], o["pairs"], date.fromisoformat(o["ship_by"]),
          date.fromisoformat(o["yarn_arrival"]), o["status"])  # fmt: skip
    for o in json.loads((SAMPLE / "orders.json").read_text(encoding="utf-8"))["orders"]
]
JUL_1 = date(2027, 7, 1).toordinal()
JUL_20 = date(2027, 7, 20).toordinal()


def _with_knit(*groups: MachineGroup, downtime=None) -> Process:
    steps = tuple(
        StepSpec("knit", groups, second_shift=True) if s.id == "knit" else s
        for s in PROCESS.steps
    )
    return replace(
        PROCESS,
        steps=steps,
        planned_downtime=PROCESS.planned_downtime if downtime is None else downtime,
    )


def test_splitting_a_step_into_equal_rate_groups_changes_nothing():
    split = _with_knit(MachineGroup("old", 8, 350), MachineGroup("new", 4, 350))
    for case in GOLDEN["cases"][:2]:
        flags = Flags(second_shift=case["flags"]["second_shift"])
        assert compute_plan(ORDERS, split, flags) == compute_plan(ORDERS, PROCESS, flags)


def test_capacity_sums_machine_groups():
    p = _with_knit(MachineGroup("old", 8, 300), MachineGroup("new", 4, 400), downtime=())
    assert capacity(p, "knit", JUL_1, Flags()) == 8 * 300 + 4 * 400
    assert capacity(p, "knit", JUL_1, Flags(second_shift=True)) == (8 * 300 + 4 * 400) * 1.5


def test_machine_level_downtime_hits_only_that_group():
    down = (Downtime("knit", date(2027, 7, 15), date(2027, 7, 25), 2, machine_id="new"),)
    p = _with_knit(MachineGroup("old", 8, 300), MachineGroup("new", 4, 400), downtime=down)
    assert capacity(p, "knit", JUL_20, Flags()) == 8 * 300 + 2 * 400
    assert capacity(p, "knit", JUL_1, Flags()) == 8 * 300 + 4 * 400


def test_step_level_downtime_takes_from_groups_in_order():
    down = (Downtime("knit", date(2027, 7, 15), date(2027, 7, 25), 10),)
    p = _with_knit(MachineGroup("old", 8, 300), MachineGroup("new", 4, 400), downtime=down)
    assert capacity(p, "knit", JUL_20, Flags()) == 0 * 300 + 2 * 400


def test_second_shift_only_on_eligible_steps():
    assert capacity(PROCESS, "knit", JUL_1, Flags(second_shift=True)) == 6300
    assert capacity(PROCESS, "pack", JUL_1, Flags(second_shift=True)) == 13 * 500


def test_route_order_is_the_process_step_order():
    # A 3-step route with custom ids: ready = last step finish + dispatch.
    p = Process(
        steps=(
            StepSpec.single("cut", 2, 1000, second_shift=False),
            StepSpec.single("sew", 5, 300, second_shift=False),
            StepSpec.single("box", 1, 5000, second_shift=False),
        ),
        dye_slots=PROCESS.dye_slots,
        dye_days=PROCESS.dye_days,
        second_shift_multiplier=1.5,
        dispatch_days=2,
    )
    plan = compute_plan([ORDERS[0]], p)["SO-1182"]
    assert list(plan.steps) == ["cut", "sew", "box"]
    assert plan.steps["cut"].start == date(2027, 7, 3)  # day after dye finish
    assert plan.steps["sew"].start == date(2027, 7, 4)  # day after the previous step starts
    # sew is the limit: 1,500 a day -> 50,000 pairs need 34 days of sewing.
    assert plan.steps["sew"].days == 34
    assert plan.ready == date.fromordinal(plan.steps["box"].finish.toordinal() + 2)
    assert plan.complete


def test_step_with_no_capacity_is_reported_incomplete():
    p = replace(PROCESS, steps=(*PROCESS.steps[:2], StepSpec("board", ()), *PROCESS.steps[3:]))
    plan = compute_plan([ORDERS[0]], p)["SO-1182"]
    assert plan.complete is False


def test_split_by_group_shares_work_by_capacity():
    p = _with_knit(MachineGroup("old", 8, 300), MachineGroup("new", 4, 400), downtime=())
    plan = compute_plan([ORDERS[0]], p)["SO-1182"]
    usage = plan.steps["knit"].usage
    parts = split_by_group(p, "knit", usage, Flags())
    for day, total in usage.items():
        assert abs(parts["old"][day] + parts["new"][day] - total) < 1e-6
        assert abs(parts["old"][day] / parts["new"][day] - 2400 / 1600) < 1e-6
