"""Load data/sample/{process,orders,suppliers}.json into the database.

Idempotent: rows are merged by primary key, so running it twice creates no duplicates.
Usage: python -m scripts.seed   (uses DATABASE_URL, default sqlite:///./supplysure.db)
"""

import json
from datetime import date
from pathlib import Path

from sqlalchemy.orm import Session

from app.db import SessionLocal, create_schema
from app.models import (
    Downtime,
    Machine,
    Material,
    Order,
    OrderMaterial,
    ProcessStep,
    ProductionSettings,
    Supplier,
)

DATA_DIR = Path(__file__).resolve().parents[1] / "data" / "sample"

# Display names for the sample steps: (step name, unit, machine group name).
STEP_DEFAULTS = {
    "knit": ("Knitting", "machines", "Knitting machines"),
    "link": ("Toe closing", "machines", "Toe closing machines"),
    "board": ("Boarding and steaming", "lines", "Boarding lines"),
    "pair": ("Pairing and quality check", "people", "Pairing and QC team"),
    "pack": ("Packing", "stations", "Packing stations"),
}


def _load(name: str, data_dir: Path) -> dict:
    return json.loads((data_dir / f"{name}.json").read_text(encoding="utf-8"))


def _unit(item: str) -> str:
    # "cotton_yarn_kg" -> "kg", "labels_pcs" -> "pcs"
    return item.rsplit("_", 1)[-1] if "_" in item else "unit"


def _name(item: str) -> str:
    # "cotton_yarn_kg" -> "Cotton yarn"
    base = item.rsplit("_", 1)[0] if "_" in item else item
    return base.replace("_", " ").capitalize()


def seed(session: Session, data_dir: Path = DATA_DIR) -> dict[str, int]:
    process = _load("process", data_dir)
    orders_doc = _load("orders", data_dir)
    suppliers_doc = _load("suppliers", data_dir)

    # Suppliers
    suppliers = {}
    for s in suppliers_doc["suppliers"]:
        row = Supplier(
            id=s["id"],
            name=s["name"],
            kind=s["kind"],
            language=s.get("language", "en"),
            discord_channel_id=s.get("discord_channel"),
            usual_slip_days=s.get("usual_slip_days", 0),
            single_source=s.get("single_source", False),
        )
        suppliers[s["id"]] = row
        session.merge(row)

    # Production route: one step per process step, one machine group per step.
    for seq, step in enumerate(process["steps"], start=1):
        name, unit, group_name = STEP_DEFAULTS.get(
            step["id"], (step["id"].capitalize(), "machines", f"{step['id']} machines")
        )
        session.merge(
            ProcessStep(
                id=step["id"],
                name=name,
                sequence=seq,
                unit=unit,
                second_shift=step.get("second_shift", step["id"] == "knit"),
            )
        )
        session.merge(
            Machine(
                id=f"machine-{step['id']}",
                name=group_name,
                step=step["id"],
                count=step["machines"],
                rate_per_machine_day=step["rate_per_machine_day"],
            )
        )

    session.merge(
        ProductionSettings(
            id=1,
            dye_slots=json.dumps(process["dye_slots"]),
            dye_days=process["dye_days"],
            second_shift_multiplier=process["second_shift_multiplier"],
            dispatch_days=process["dispatch_days"],
            transit_days=process.get("transit_days", 0),
        )
    )

    # Planned downtime, attached to the step's machine group.
    for d in process.get("planned_downtime", []):
        session.merge(
            Downtime(
                id=f"downtime-{d['step']}-{d['from']}",
                step=d["step"],
                machine_id=f"machine-{d['step']}",
                from_date=date.fromisoformat(d["from"]),
                to_date=date.fromisoformat(d["to"]),
                machines_down=d["machines_down"],
                planned=True,
                reason=d.get("reason", "Planned maintenance"),
            )
        )

    # Orders
    order_meta = {}
    for o in orders_doc["orders"]:
        order_meta[o["id"]] = o
        session.merge(
            Order(
                id=o["id"],
                buyer=o["buyer"],
                pairs=o["pairs"],
                ship_by=date.fromisoformat(o["ship_by"]),
                status=o["status"],
                grams_per_pair=o["grams_per_pair"],
                priority=0,
                yarn_arrival=(
                    date.fromisoformat(o["yarn_arrival"]) if o.get("yarn_arrival") else None
                ),
            )
        )

    # Materials per order ("materials_<ORDER_ID>" keys). A material is shared across
    # orders; its stock and MOQ come from the first order that mentions it.
    materials_seen: set[str] = set()
    for key, items in orders_doc.items():
        if not key.startswith("materials_"):
            continue
        order_id = key[len("materials_") :]
        order = order_meta[order_id]
        for item in items:
            mat_id = item["item"]
            supplier = suppliers.get(item["supplier"])
            if mat_id not in materials_seen:
                materials_seen.add(mat_id)
                session.merge(
                    Material(
                        id=mat_id,
                        name=_name(mat_id),
                        unit=_unit(mat_id),
                        moq=item["moq"],
                        on_hand=item["on_hand"],
                        supplier_id=supplier.id if supplier else None,
                    )
                )
            # Yarn arrival date from the order applies to yarn suppliers only.
            expected = None
            if supplier is not None and supplier.kind == "yarn" and order.get("yarn_arrival"):
                expected = date.fromisoformat(order["yarn_arrival"])
            session.merge(
                OrderMaterial(
                    order_id=order_id,
                    material_id=mat_id,
                    qty_per_pair=item["need"] / order_meta[order_id]["pairs"],
                    waste_pct=0,
                    expected_arrival=expected,
                )
            )

    session.commit()
    return {
        "suppliers": session.query(Supplier).count(),
        "orders": session.query(Order).count(),
        "materials": session.query(Material).count(),
        "process_steps": session.query(ProcessStep).count(),
        "machines": session.query(Machine).count(),
        "downtime": session.query(Downtime).count(),
        "order_materials": session.query(OrderMaterial).count(),
    }


def main() -> None:
    create_schema()
    with SessionLocal() as session:
        counts = seed(session)
    print("Seeded:", counts)


if __name__ == "__main__":
    main()
