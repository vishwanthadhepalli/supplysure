"""Golden planner tests. Never edit data/sample/golden_planner.json to make these pass."""

import json
from datetime import date
from pathlib import Path

import pytest

from app.planner import (
    Flags,
    Order,
    Process,
    capacity,
    compute_plan,
    downtime_from_dict,
    net_material,
    priority_sequence,
)

SAMPLE = Path(__file__).resolve().parents[1] / "data" / "sample"
GOLDEN = json.loads((SAMPLE / "golden_planner.json").read_text(encoding="utf-8"))
PROCESS_DOC = json.loads((SAMPLE / "process.json").read_text(encoding="utf-8"))
ORDERS_DOC = json.loads((SAMPLE / "orders.json").read_text(encoding="utf-8"))

PROCESS = Process.from_dict(PROCESS_DOC)
ORDERS = [
    Order(
        id=o["id"],
        pairs=o["pairs"],
        ship_by=date.fromisoformat(o["ship_by"]),
        yarn_arrival=date.fromisoformat(o["yarn_arrival"]),
        status=o["status"],
    )
    for o in ORDERS_DOC["orders"]
]


def _flags(raw: dict) -> Flags:
    return Flags(
        second_shift=raw["second_shift"],
        new_request_ahead=raw["new_request_ahead_of_SO_1190"],
        yarn_delay_days=raw["yarn_delay_days"],
        extra_downtime=tuple(downtime_from_dict(d) for d in raw["extra_downtime"]),
    )


@pytest.mark.parametrize("case", GOLDEN["cases"], ids=[c["name"] for c in GOLDEN["cases"]])
def test_golden_case(case):
    plan = compute_plan(ORDERS, PROCESS, _flags(case["flags"]))
    assert set(plan) == set(case["orders"])
    for order_id, expected in case["orders"].items():
        got = plan[order_id]
        actual = {
            "dye_start": got.dye_start.isoformat(),
            "dye_finish": got.dye_finish.isoformat(),
            "knit_start": got.steps["knit"].start.isoformat(),
            "knit_finish": got.steps["knit"].finish.isoformat(),
            "pack_finish": got.steps["pack"].finish.isoformat(),
            "ready": got.ready.isoformat(),
            "ship_by": got.ship_by.isoformat(),
            "slack_days": got.slack_days,
        }
        assert actual == expected, order_id


def test_default_priority_matches_golden():
    assert [o.id for o in priority_sequence(ORDERS, False)] == GOLDEN["priority_default"]
    assert [o.id for o in priority_sequence(ORDERS, True)] == ["SO-1182", "SO-1195", "SO-1190"]


def test_capacity_rules():
    jul_01 = date(2027, 7, 1).toordinal()
    jul_20 = date(2027, 7, 20).toordinal()
    assert capacity(PROCESS, "knit", jul_01, Flags()) == 12 * 350
    assert capacity(PROCESS, "knit", jul_20, Flags()) == 10 * 350  # planned maintenance
    assert capacity(PROCESS, "knit", jul_01, Flags(second_shift=True)) == 6300
    assert capacity(PROCESS, "link", jul_01, Flags(second_shift=True)) == 6 * 800


def test_plan_is_pure_and_repeatable():
    flags = _flags(GOLDEN["cases"][0]["flags"])
    assert compute_plan(ORDERS, PROCESS, flags) == compute_plan(ORDERS, PROCESS, flags)


@pytest.mark.parametrize(
    "expected", GOLDEN["materials_netting"]["items"], ids=lambda e: e["item"]
)
def test_materials_netting_so_1182(expected):
    pairs = next(o.pairs for o in ORDERS if o.id == "SO-1182")
    source = next(m for m in ORDERS_DOC["materials_SO-1182"] if m["item"] == expected["item"])
    result = net_material(
        pairs=pairs,
        qty_per_pair=source["need"] / pairs,
        waste_pct=0,
        on_hand=source["on_hand"],
        moq=source["moq"],
    )
    assert result.need == expected["need"]
    assert result.buy == expected["buy"]


def test_netting_with_waste_rounds_up():
    # 1000 pairs x 0.1 kg x 1.05 = 105 kg; 105 - 20 on hand = 85 -> 2 x MOQ 50 = 100.
    result = net_material(pairs=1000, qty_per_pair=0.1, waste_pct=5, on_hand=20, moq=50)
    assert (result.need, result.buy) == (105, 100)
    assert net_material(pairs=10, qty_per_pair=1, waste_pct=0, on_hand=50, moq=25).buy == 0
