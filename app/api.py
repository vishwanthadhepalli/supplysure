"""Read API for the web app: plan, orders, ledger, graph, activity.

Every endpoint here is GET (D-11): supplier data is written only by intake (Discord) and never
from the web. Factory setup (machines, route, downtime) is edited through app/setup_api.py.
What-if flags are query parameters and never change stored data.
"""

from __future__ import annotations

import json
from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.brain import COLUMNS, TYPE_LABELS, build_graph, single_source_materials, trace
from app.db import get_session
from app.models import ActionLog, Material, Message, Promise, Supplier
from app.planner import Downtime, Flags, OrderSchedule
from app.planning import (
    SCENARIOS,
    PlanRun,
    capacity_series,
    nominal_capacity,
    order_netting,
    risk_notes,
    run_plan,
    verdict,
)

router = APIRouter(prefix="/api")

PROMISE_STATUSES = ("open", "needs_clarification", "kept", "slipped", "cancelled")


def _iso(d: date | None) -> str | None:
    return d.isoformat() if d else None


def parse_downtime(spec: str) -> Downtime:
    """"step:YYYY-MM-DD:YYYY-MM-DD:machines", e.g. "knit:2027-07-03:2027-07-10:2"."""
    try:
        step, start, end, n = spec.split(":")
        dt = Downtime(step, date.fromisoformat(start), date.fromisoformat(end), int(n))
    except ValueError as exc:
        raise HTTPException(422, f"bad extra_down {spec!r}: use step:from:to:machines") from exc
    if not step or dt.to_date < dt.from_date or dt.machines_down < 0:
        raise HTTPException(422, f"bad extra_down {spec!r}")
    return dt


def plan_flags(
    second_shift: bool = False,
    new_request_ahead: bool = False,
    yarn_delay_days: int = Query(0, ge=0, le=30),
    extra_down: list[str] = Query([]),
) -> Flags:
    return Flags(
        second_shift=second_shift,
        new_request_ahead=new_request_ahead,
        yarn_delay_days=yarn_delay_days,
        extra_downtime=tuple(parse_downtime(s) for s in extra_down),
    )


def checked_run(session: Session, flags: Flags, use_promises: bool = True) -> PlanRun:
    """run_plan, with what-if downtime checked against the route's steps."""
    run = run_plan(session, flags, use_promises)
    unknown = [d.step for d in flags.extra_downtime if d.step not in run.steps]
    if unknown:
        raise HTTPException(422, f"unknown step in extra_down: {', '.join(unknown)}")
    return run


def _flags_json(flags: Flags) -> dict:
    return {
        "second_shift": flags.second_shift,
        "new_request_ahead": flags.new_request_ahead,
        "yarn_delay_days": flags.yarn_delay_days,
        "extra_down": [
            f"{d.step}:{d.from_date}:{d.to_date}:{d.machines_down}" for d in flags.extra_downtime
        ],
    }


def _schedule_json(run: PlanRun, oid: str, baseline: OrderSchedule | None) -> dict:
    row, sched, yarn = run.orders[oid], run.schedules[oid], run.yarn[oid]
    v = verdict(sched)
    last_finish = sched.steps[run.process.step_ids[-1]].finish
    return {
        "id": oid,
        "buyer": row.buyer,
        "pairs": row.pairs,
        "status": row.status,
        "ship_by": _iso(row.ship_by),
        "yarn": {
            "date": _iso(yarn.date),
            "planned": _iso(row.yarn_arrival),
            "source": yarn.source,
            "promise_id": yarn.promise_id,
            "quote": yarn.quote,
        },
        "dye": {"start": _iso(sched.dye_start), "finish": _iso(sched.dye_finish)},
        "steps": [
            {
                "id": sid,
                "name": run.step_name(sid),
                "start": _iso(s.start),
                "finish": _iso(s.finish),
                "days": s.days,
                "usage": {k.isoformat(): v for k, v in s.usage.items()},
            }
            for sid, s in sched.steps.items()
        ],
        "dispatch": {"start": _iso(last_finish + timedelta(days=1)), "finish": _iso(sched.ready)},
        "complete": sched.complete,
        "ready": _iso(sched.ready),
        "slack_days": sched.slack_days,
        "verdict": {"level": v.level, "label": v.label, "detail": v.detail},
        "baseline": (
            {"ready": _iso(baseline.ready), "slack_days": baseline.slack_days} if baseline else None
        ),
        "risks": risk_notes(run, oid),
    }


def _plan_json(session: Session, flags: Flags, use_promises: bool) -> dict:
    run = checked_run(session, flags, use_promises)
    base = run_plan(session, Flags(), use_promises)
    orders = [
        _schedule_json(run, oid, base.schedules.get(oid)) for oid in run.schedules
    ]
    chart = None
    if run.schedules:
        first = run.process.step_ids[0]
        start = min(s.steps[first].start for s in run.schedules.values()) - timedelta(days=1)
        end = max(max(s.ready, s.ship_by) for s in run.schedules.values()) + timedelta(days=1)
        chart = {
            "start": _iso(start),
            "end": _iso(end),
            "steps": [
                {
                    "id": spec.id,
                    "name": run.step_name(spec.id),
                    "unit": run.steps[spec.id].unit,
                    "machines": spec.machines,
                    "groups": [
                        {
                            "id": g.id,
                            "name": run.machines[g.id].name,
                            "count": g.count,
                            "rate": g.rate_per_machine_day,
                        }
                        for g in spec.groups
                    ],
                    "nominal": nominal_capacity(run.process, spec.id, flags),
                    "capacity": capacity_series(run, spec.id, start, end),
                }
                for spec in run.process.steps
            ],
            "downtime": [
                {
                    "step": d.step,
                    "machine_id": d.machine_id,
                    "from": _iso(d.from_date),
                    "to": _iso(d.to_date),
                    "machines_down": d.machines_down,
                    "planned": i < len(run.process.planned_downtime),
                }
                for i, d in enumerate((*run.process.planned_downtime, *flags.extra_downtime))
            ],
        }
    return {
        "flags": _flags_json(flags),
        "use_promises": use_promises,
        "priority": list(run.schedules),
        "orders": orders,
        "unplanned": run.unplanned,
        "problems": run.problems,
        "chart": chart,
        "process": {
            "dye_slots": [_iso(date.fromordinal(s)) for s in run.process.dye_slots],
            "dye_days": run.process.dye_days,
            "dispatch_days": run.process.dispatch_days,
            "second_shift_multiplier": run.process.second_shift_multiplier,
        },
    }


@router.get("/plan")
def get_plan(
    flags: Flags = Depends(plan_flags),
    use_promises: bool = True,
    session: Session = Depends(get_session),
) -> dict:
    return _plan_json(session, flags, use_promises)


@router.get("/plan/scenarios")
def get_scenarios(use_promises: bool = True, session: Session = Depends(get_session)) -> dict:
    out = []
    for key, label, flags in SCENARIOS:
        run = run_plan(session, flags, use_promises)
        if run.problems:  # e.g. the owner removed the step a preset refers to
            continue
        out.append(
            {
                "key": key,
                "label": label,
                "flags": _flags_json(flags),
                "orders": {
                    oid: {
                        "ready": _iso(s.ready),
                        "slack_days": s.slack_days,
                        "verdict": verdict(s).level,
                    }
                    for oid, s in run.schedules.items()
                },
            }
        )
    return {"scenarios": out}


def _promise_json(p: Promise, sup: Supplier | None, mat: Material | None, msg: Message) -> dict:
    return {
        "id": p.id,
        "status": p.status,
        "supplier": {"id": p.supplier_id, "name": sup.name if sup else p.supplier_id},
        "material": (
            {"id": mat.id, "name": mat.name, "unit": mat.unit}
            if mat
            else {"id": p.material_id, "name": p.material_id, "unit": ""}
        ),
        "order_id": p.order_id,
        "quantity": p.quantity,
        "promised_date": _iso(p.promised_date),
        "confidence": p.confidence,
        "quote": p.quote,
        "quote_verified": p.quote_verified,
        "clarification_question": p.clarification_question,
        "created_at": p.created_at.isoformat(),
        "message": {
            "id": msg.id,
            "source": msg.source,
            "kind": msg.kind,
            "received_at": msg.received_at.isoformat(),
            "transcript_lang": msg.transcript_lang,
            "text": msg.english_text or msg.raw_text or msg.transcript,
        },
    }


def _promises(
    session: Session, status: str | None = None, order_id: str | None = None
) -> list[dict]:
    q = (
        select(Promise, Supplier, Material, Message)
        .join(Message, Promise.message_id == Message.id)
        .outerjoin(Supplier, Promise.supplier_id == Supplier.id)
        .outerjoin(Material, Promise.material_id == Material.id)
        .order_by(Message.received_at.desc(), Promise.id)
    )
    if status:
        q = q.where(Promise.status == status)
    if order_id:
        q = q.where(Promise.order_id == order_id)
    return [_promise_json(p, s, m, msg) for p, s, m, msg in session.execute(q).all()]


@router.get("/promises")
def get_promises(
    status: str | None = Query(None), session: Session = Depends(get_session)
) -> dict:
    if status and status not in PROMISE_STATUSES:
        raise HTTPException(422, f"status must be one of {', '.join(PROMISE_STATUSES)}")
    all_rows = _promises(session)
    counts = {s: sum(1 for p in all_rows if p["status"] == s) for s in PROMISE_STATUSES}
    rows = [p for p in all_rows if p["status"] == status] if status else all_rows
    return {"promises": rows, "counts": counts, "total": len(all_rows)}


@router.get("/orders/{order_id}")
def get_order(
    order_id: str,
    flags: Flags = Depends(plan_flags),
    use_promises: bool = True,
    session: Session = Depends(get_session),
) -> dict:
    run = checked_run(session, flags, use_promises)
    row = run.orders.get(order_id)
    if row is None:
        raise HTTPException(404, f"unknown order {order_id}")
    base = run_plan(session, Flags(), use_promises)
    plan = (
        _schedule_json(run, order_id, base.schedules.get(order_id))
        if order_id in run.schedules
        else None
    )
    materials = [
        {
            "id": r.material_id,
            "name": r.name,
            "unit": r.unit,
            "need": r.netting.need,
            "on_hand": r.on_hand,
            "moq": r.moq,
            "buy": r.netting.buy,
            "supplier": r.supplier_name,
            "expected_arrival": _iso(r.expected_arrival),
        }
        for r in order_netting(session, row)
    ]
    return {
        "order": plan,
        "row": {  # the stored order, also when it cannot be planned yet
            "id": row.id,
            "buyer": row.buyer,
            "pairs": row.pairs,
            "status": row.status,
            "ship_by": _iso(row.ship_by),
            "yarn_arrival": _iso(row.yarn_arrival),
            "grams_per_pair": row.grams_per_pair,
        },
        "materials": materials,
        "promises": _promises(session, order_id=order_id),
    }


@router.get("/graph")
def get_graph(
    flags: Flags = Depends(plan_flags),
    use_promises: bool = True,
    session: Session = Depends(get_session),
) -> dict:
    graph = build_graph(session, checked_run(session, flags, use_promises))
    return {
        "types": TYPE_LABELS,
        "columns": COLUMNS,
        "nodes": [vars(n) for n in graph.nodes.values()],
        "edges": [vars(e) for e in graph.edges],
        "single_source": single_source_materials(graph),
    }


@router.get("/graph/trace")
def get_trace(
    node: str,
    direction: str = Query("down", pattern="^(up|down)$"),
    flags: Flags = Depends(plan_flags),
    use_promises: bool = True,
    session: Session = Depends(get_session),
) -> dict:
    graph = build_graph(session, checked_run(session, flags, use_promises))
    if node not in graph.nodes:
        raise HTTPException(404, f"unknown node {node}")
    reached = trace(graph, node, direction)
    return {
        "node": node,
        "direction": direction,
        "reached": reached,
        "orders": [n for n in reached if graph.nodes[n].type == "order"],
    }


@router.get("/activity")
def get_activity(
    limit: int = Query(50, ge=1, le=500), session: Session = Depends(get_session)
) -> dict:
    rows = session.scalars(select(ActionLog).order_by(ActionLog.at.desc()).limit(limit))
    return {
        "actions": [
            {
                "id": a.id,
                "at": a.at.isoformat(),
                "actor": a.actor,
                "action": a.action,
                "payload": json.loads(a.payload) if a.payload else None,
            }
            for a in rows
        ]
    }
