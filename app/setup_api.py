"""Factory setup API: production route (steps), machine groups, downtime, outside stages.

This is the owner's own factory data, so the web app may edit it (D-11, updated in D-45).
Supplier data (messages, promises, reminders) stays read-only here. Every write is validated
and logged in action_log with actor "owner". Plans are recomputed from this data on each read.
"""

from __future__ import annotations

import json
import re
from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api import _iso, checked_run, plan_flags
from app.audit import log_action
from app.db import get_session
from app.models import Downtime, Machine, Order, ProcessStep, ProductionSettings
from app.planner import Flags
from app.planning import (
    MachineLoad,
    PlanRun,
    bottleneck_step,
    machine_loads,
    nominal_capacity,
    production_settings,
)

router = APIRouter(prefix="/api")
ACTOR = "owner"


# ---------- schemas ----------


class MachineIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    step: str = Field(min_length=1, max_length=40)
    count: int = Field(ge=1, le=10_000)
    rate_per_machine_day: int = Field(ge=1, le=10_000_000)
    notes: str | None = Field(None, max_length=500)


class DowntimeIn(BaseModel):
    machine_id: str
    from_date: date
    to_date: date
    machines_down: int = Field(ge=1, le=10_000)
    planned: bool = True  # False: probable breakdown
    reason: str | None = Field(None, max_length=200)

    @model_validator(mode="after")
    def _dates(self):
        if self.to_date < self.from_date:
            raise ValueError("to_date is before from_date")
        if (self.to_date - self.from_date).days > 366:
            raise ValueError("downtime longer than a year")
        return self


ORDER_ID = r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,39}$"


class OrderIn(BaseModel):
    id: str | None = Field(None, pattern=ORDER_ID)  # None: next SO-number
    buyer: str = Field(min_length=1, max_length=120)
    pairs: int = Field(ge=1, le=10_000_000)
    ship_by: date
    status: Literal["booked", "tentative"] = "booked"
    grams_per_pair: float | None = Field(None, gt=0, le=2000)  # None: copy the latest order's
    yarn_arrival: date | None = None  # None: not planned until a yarn date is known
    priority: int = Field(0, ge=0, le=100)


class OrderUpdate(BaseModel):
    buyer: str | None = Field(None, min_length=1, max_length=120)
    pairs: int | None = Field(None, ge=1, le=10_000_000)
    ship_by: date | None = None
    status: Literal["booked", "tentative", "done"] | None = None
    grams_per_pair: float | None = Field(None, gt=0, le=2000)
    yarn_arrival: date | None = None


class StepIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    unit: str = Field("machines", min_length=1, max_length=30)
    second_shift: bool = False


class StepUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=80)
    unit: str | None = Field(None, min_length=1, max_length=30)
    second_shift: bool | None = None


class MoveIn(BaseModel):
    direction: Literal["up", "down"]


class SettingsIn(BaseModel):
    dye_slots: list[date] = Field(max_length=200)
    dye_days: int = Field(ge=0, le=60)
    second_shift_multiplier: float = Field(ge=1, le=3)
    dispatch_days: int = Field(ge=0, le=60)
    transit_days: int = Field(0, ge=0, le=180)


# ---------- helpers ----------


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40] or "item"


def _unique_id(session: Session, model, base: str) -> str:
    candidate, n = base, 2
    while session.get(model, candidate) is not None:
        candidate, n = f"{base}-{n}", n + 1
    return candidate


def _get_or_404(session: Session, model, key: str, what: str):
    row = session.get(model, key)
    if row is None:
        raise HTTPException(404, f"unknown {what} {key}")
    return row


def _renumber(session: Session) -> None:
    steps = session.scalars(select(ProcessStep).order_by(ProcessStep.sequence, ProcessStep.id))
    for i, s in enumerate(steps, start=1):
        s.sequence = i


def _machine_json(load: MachineLoad) -> dict:
    m, step = load.machine, load.step
    return {
        "id": m.id,
        "name": m.name,
        "step": {
            "id": m.step,
            "name": step.name if step else m.step,
            "unit": step.unit if step else "",
        },
        "count": m.count,
        "rate_per_machine_day": m.rate_per_machine_day,
        "notes": m.notes,
        "nominal": round(load.nominal, 2),
        "available": round(load.available, 2),
        "used": round(load.used, 2),
        "utilisation": round(load.utilisation, 4),
        "peak": {
            "day": _iso(load.peak_day),
            "utilisation": round(load.peak_utilisation, 4),
        },
        "daily": [
            {"date": _iso(d), "capacity": round(c, 2), "used": round(u, 2)}
            for d, c, u in load.daily
        ],
        "orders": [
            {
                "id": o.order_id,
                "pairs": round(o.pairs),
                "start": _iso(o.start),
                "finish": _iso(o.finish),
            }
            for o in load.orders
        ],
        "downtime": [
            {
                "id": i.row.id,
                "from": _iso(i.row.from_date),
                "to": _iso(i.row.to_date),
                "machines_down": i.row.machines_down,
                "planned": i.row.planned,
                "reason": i.row.reason,
                "lost": round(i.lost),
                "affected_orders": i.affected_orders,
            }
            for i in load.downtime
        ],
    }


def _horizon_json(run: PlanRun) -> dict | None:
    h = run.horizon
    return {"start": _iso(h[0]), "end": _iso(h[1])} if h else None


# ---------- orders (the owner's buyer orders, D-60) ----------


def next_order_id(session: Session) -> str:
    """SO-<n>, one above the highest SO number in use."""
    nums = [
        int(m.group(1))
        for oid in session.scalars(select(Order.id))
        if (m := re.fullmatch(r"SO-(\d+)", oid))
    ]
    return f"SO-{(max(nums) + 1) if nums else 1001}"


def default_grams_per_pair(session: Session) -> float:
    latest = session.scalar(select(Order).order_by(Order.id.desc()))
    return float(latest.grams_per_pair) if latest else 90.0


@router.post("/orders", status_code=201)
def create_order(body: OrderIn, session: Session = Depends(get_session)):
    oid = (body.id or next_order_id(session)).upper()
    if session.get(Order, oid) is not None:
        raise HTTPException(409, f"Order {oid} already exists.")
    row = Order(
        id=oid,
        buyer=body.buyer.strip(),
        pairs=body.pairs,
        ship_by=body.ship_by,
        status=body.status,
        grams_per_pair=body.grams_per_pair or default_grams_per_pair(session),
        priority=body.priority,
        yarn_arrival=body.yarn_arrival,
    )
    session.add(row)
    log_action(session, ACTOR, "order_created",
               {"order_id": oid, **body.model_dump(mode="json")})  # fmt: skip
    session.commit()
    return {"id": oid}


@router.patch("/orders/{order_id}")
def update_order(order_id: str, body: OrderUpdate, session: Session = Depends(get_session)):
    row = _get_or_404(session, Order, order_id, "order")
    changes = body.model_dump(exclude_none=True)
    if not changes:
        raise HTTPException(422, "Nothing to change.")
    before = {k: getattr(row, k) for k in changes}
    for k, v in changes.items():
        setattr(row, k, v)
    log_action(session, ACTOR, "order_updated",
               {"order_id": order_id, "before": before, "after": changes})  # fmt: skip
    session.commit()
    return {"id": order_id}


# ---------- machines ----------


@router.get("/machines")
def list_machines(flags: Flags = Depends(plan_flags), session: Session = Depends(get_session)):
    run = checked_run(session, flags)
    return {
        "machines": [_machine_json(m) for m in machine_loads(run)],
        "steps": [{"id": s.id, "name": s.name, "unit": s.unit} for s in run.steps.values()],
        "bottleneck": bottleneck_step(run),
        "horizon": _horizon_json(run),
        "problems": run.problems,
    }


@router.get("/machines/{machine_id}")
def get_machine(
    machine_id: str, flags: Flags = Depends(plan_flags), session: Session = Depends(get_session)
):
    _get_or_404(session, Machine, machine_id, "machine")
    run = checked_run(session, flags)
    load = next(m for m in machine_loads(run) if m.machine.id == machine_id)
    return {
        "machine": _machine_json(load),
        "horizon": _horizon_json(run),
        "bottleneck": bottleneck_step(run),
        "problems": run.problems,
    }


@router.post("/machines", status_code=201)
def add_machine(body: MachineIn, session: Session = Depends(get_session)):
    _get_or_404(session, ProcessStep, body.step, "step")
    row = Machine(
        id=_unique_id(session, Machine, f"machine-{_slug(body.name)}"), **body.model_dump()
    )
    session.add(row)
    log_action(session, ACTOR, "machine_added", {"machine_id": row.id, **body.model_dump()})
    session.commit()
    return {"id": row.id}


@router.put("/machines/{machine_id}")
def update_machine(machine_id: str, body: MachineIn, session: Session = Depends(get_session)):
    row = _get_or_404(session, Machine, machine_id, "machine")
    _get_or_404(session, ProcessStep, body.step, "step")
    before = {"name": row.name, "step": row.step, "count": row.count,
              "rate_per_machine_day": row.rate_per_machine_day}  # fmt: skip
    for k, v in body.model_dump().items():
        setattr(row, k, v)
    for d in session.scalars(select(Downtime).where(Downtime.machine_id == machine_id)):
        d.step = body.step
        d.machines_down = min(d.machines_down, body.count)
    log_action(
        session, ACTOR, "machine_updated", {"machine_id": machine_id, "before": before,
                                            "after": body.model_dump()}
    )  # fmt: skip
    session.commit()
    return {"id": machine_id}


@router.delete("/machines/{machine_id}")
def delete_machine(machine_id: str, session: Session = Depends(get_session)):
    row = _get_or_404(session, Machine, machine_id, "machine")
    removed = 0
    for d in session.scalars(select(Downtime).where(Downtime.machine_id == machine_id)):
        session.delete(d)
        removed += 1
    session.delete(row)
    log_action(
        session, ACTOR, "machine_deleted",
        {"machine_id": machine_id, "name": row.name, "downtime_removed": removed},
    )  # fmt: skip
    session.commit()
    return {"deleted": machine_id}


# ---------- downtime ----------


@router.post("/downtime", status_code=201)
def add_downtime(body: DowntimeIn, session: Session = Depends(get_session)):
    machine = _get_or_404(session, Machine, body.machine_id, "machine")
    if body.machines_down > machine.count:
        raise HTTPException(422, f"{machine.name} has only {machine.count}")
    base = f"downtime-{machine.id}-{body.from_date.isoformat()}"
    row = Downtime(
        id=_unique_id(session, Downtime, base),
        step=machine.step,
        machine_id=machine.id,
        from_date=body.from_date,
        to_date=body.to_date,
        machines_down=body.machines_down,
        planned=body.planned,
        reason=body.reason,
    )
    session.add(row)
    log_action(session, ACTOR, "downtime_added", {"downtime_id": row.id, **body.model_dump()})
    session.commit()
    return {"id": row.id}


@router.delete("/downtime/{downtime_id}")
def delete_downtime(downtime_id: str, session: Session = Depends(get_session)):
    row = _get_or_404(session, Downtime, downtime_id, "downtime")
    session.delete(row)
    log_action(session, ACTOR, "downtime_deleted", {"downtime_id": downtime_id})
    session.commit()
    return {"deleted": downtime_id}


# ---------- production plan: route and outside stages ----------


@router.get("/production-plan")
def get_production_plan(
    flags: Flags = Depends(plan_flags), session: Session = Depends(get_session)
):
    run = checked_run(session, flags)
    loads = {m.machine.id: m for m in machine_loads(run)}
    bottleneck = bottleneck_step(run)
    steps = []
    for meta in run.steps.values():
        groups = [m for m in loads.values() if m.machine.step == meta.id]
        available = sum(g.available for g in groups)
        used = sum(g.used for g in groups)
        steps.append(
            {
                "id": meta.id,
                "name": meta.name,
                "unit": meta.unit,
                "sequence": meta.sequence,
                "second_shift": meta.second_shift,
                "nominal": nominal_capacity(run.process, meta.id, run.flags) if groups else 0,
                "utilisation": round(used / available, 4) if available else 0,
                "bottleneck": meta.id == bottleneck,
                "machines": [
                    {
                        "id": g.machine.id,
                        "name": g.machine.name,
                        "count": g.machine.count,
                        "rate_per_machine_day": g.machine.rate_per_machine_day,
                        "nominal": round(g.nominal, 2),
                        "downtime": len(g.downtime),
                    }
                    for g in groups
                ],
            }
        )
    return {
        "steps": steps,
        "settings": production_settings(session),
        "horizon": _horizon_json(run),
        "problems": run.problems,
    }


@router.post("/process/steps", status_code=201)
def add_step(body: StepIn, session: Session = Depends(get_session)):
    last = session.scalar(select(func.max(ProcessStep.sequence))) or 0
    row = ProcessStep(
        id=_unique_id(session, ProcessStep, _slug(body.name)),
        name=body.name,
        unit=body.unit,
        second_shift=body.second_shift,
        sequence=last + 1,
    )
    session.add(row)
    log_action(session, ACTOR, "step_added", {"step_id": row.id, **body.model_dump()})
    session.commit()
    return {"id": row.id}


@router.put("/process/steps/{step_id}")
def update_step(step_id: str, body: StepUpdate, session: Session = Depends(get_session)):
    row = _get_or_404(session, ProcessStep, step_id, "step")
    changes = body.model_dump(exclude_none=True)
    for k, v in changes.items():
        setattr(row, k, v)
    log_action(session, ACTOR, "step_updated", {"step_id": step_id, **changes})
    session.commit()
    return {"id": step_id}


@router.post("/process/steps/{step_id}/move")
def move_step(step_id: str, body: MoveIn, session: Session = Depends(get_session)):
    _get_or_404(session, ProcessStep, step_id, "step")
    _renumber(session)
    steps = list(session.scalars(select(ProcessStep).order_by(ProcessStep.sequence)))
    i = next(i for i, s in enumerate(steps) if s.id == step_id)
    j = i - 1 if body.direction == "up" else i + 1
    if 0 <= j < len(steps):
        steps[i].sequence, steps[j].sequence = steps[j].sequence, steps[i].sequence
        log_action(session, ACTOR, "step_moved", {"step_id": step_id, "direction": body.direction})
    session.commit()
    order = session.scalars(select(ProcessStep.id).order_by(ProcessStep.sequence)).all()
    return {"route": order}


@router.delete("/process/steps/{step_id}")
def delete_step(step_id: str, session: Session = Depends(get_session)):
    row = _get_or_404(session, ProcessStep, step_id, "step")
    assigned = session.scalars(select(Machine.name).where(Machine.step == step_id)).all()
    if assigned:
        raise HTTPException(
            409, f"Move or delete the machines on this step first: {', '.join(assigned)}"
        )
    for d in session.scalars(select(Downtime).where(Downtime.step == step_id)):
        session.delete(d)
    session.delete(row)
    session.flush()
    _renumber(session)
    log_action(session, ACTOR, "step_deleted", {"step_id": step_id, "name": row.name})
    session.commit()
    return {"deleted": step_id}


@router.put("/process/settings")
def update_settings(body: SettingsIn, session: Session = Depends(get_session)):
    row = session.get(ProductionSettings, 1)
    if row is None:
        row = ProductionSettings(id=1)
        session.add(row)
    row.dye_slots = json.dumps(sorted({d.isoformat() for d in body.dye_slots}))
    row.dye_days = body.dye_days
    row.second_shift_multiplier = body.second_shift_multiplier
    row.dispatch_days = body.dispatch_days
    row.transit_days = body.transit_days
    log_action(session, ACTOR, "settings_updated", body.model_dump(mode="json"))
    session.commit()
    return production_settings(session)
