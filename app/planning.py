"""Bridge between the database and the pure planner (app/planner).

Loads the production route, machine groups, downtime and settings from the DB, applies open
yarn promises (PLANNER_SPEC "Using promises"), runs the plan and derives the facts the web
app shows: verdicts, risk notes, netting, and per-machine load. All dates and numbers here
come from code; nothing is computed by a model.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Downtime as DowntimeRow
from app.models import (
    Machine,
    Material,
    Message,
    OrderMaterial,
    ProcessStep,
    ProductionSettings,
    Promise,
    Supplier,
)
from app.models import Order as OrderRow
from app.planner import (
    Downtime,
    Flags,
    MachineGroup,
    Netting,
    Order,
    OrderSchedule,
    Process,
    StepSpec,
    capacity,
    compute_plan,
    group_capacities,
    net_material,
    split_by_group,
)

DEFAULT_PROCESS_PATH = Path(__file__).resolve().parents[1] / "data" / "sample" / "process.json"

# The 7 golden scenarios, offered as presets in the web app.
SCENARIOS: list[tuple[str, str, Flags]] = [
    ("baseline", "Base plan", Flags()),
    ("second_shift", "Second shift on knitting", Flags(second_shift=True)),
    ("new_request_ahead", "SO-1195 ahead of SO-1190", Flags(new_request_ahead=True)),
    ("both", "Second shift and SO-1195 ahead", Flags(second_shift=True, new_request_ahead=True)),
    (
        "knit_down_jul3_10",
        "2 knitting machines down Jul 3 to 10",
        Flags(
            extra_downtime=(Downtime("knit", date(2027, 7, 3), date(2027, 7, 10), 2),),
        ),
    ),
    ("yarn_late_2", "Yarn 2 days late", Flags(yarn_delay_days=2)),
    ("yarn_late_4", "Yarn 4 days late", Flags(yarn_delay_days=4)),
]


def fmt(d: date) -> str:
    return f"{d:%b} {d.day}"


def plural(n: int) -> str:
    return f"{n} day" if abs(n) == 1 else f"{n} days"


def is_yarn_material(material_id: str | None) -> bool:
    """The dye-house input. Elastane is also bought from a yarn supplier but is not dyed."""
    return bool(material_id) and "yarn" in material_id


@dataclass(frozen=True)
class StepMeta:
    id: str
    name: str
    unit: str
    sequence: int
    second_shift: bool


@dataclass(frozen=True)
class YarnSource:
    date: date
    source: str  # "planned" or "promise"
    promise_id: str | None = None
    quote: str | None = None
    supplier_id: str | None = None


@dataclass(frozen=True)
class Verdict:
    level: str  # ok, warn, risk
    label: str
    detail: str


@dataclass
class PlanRun:
    process: Process
    flags: Flags
    steps: dict[str, StepMeta]  # route order
    machines: dict[str, Machine]
    downtime_rows: list[DowntimeRow]
    orders: dict[str, OrderRow]
    inputs: dict[str, Order]
    yarn: dict[str, YarnSource]
    schedules: dict[str, OrderSchedule]
    unplanned: list[str]
    transit_days: int = 0
    problems: list[str] = field(default_factory=list)

    def step_name(self, step_id: str) -> str:
        meta = self.steps.get(step_id)
        return meta.name if meta else step_id

    @property
    def horizon(self) -> tuple[date, date] | None:
        """First in-house work day to last finishing day, across all orders."""
        if not self.schedules:
            return None
        first, last = self.process.step_ids[0], self.process.step_ids[-1]
        return (
            min(s.steps[first].start for s in self.schedules.values()),
            max(s.steps[last].finish for s in self.schedules.values()),
        )


def production_settings(session: Session) -> dict:
    row = session.get(ProductionSettings, 1)
    if row is not None:
        return {
            "dye_slots": json.loads(row.dye_slots),
            "dye_days": row.dye_days,
            "second_shift_multiplier": row.second_shift_multiplier,
            "dispatch_days": row.dispatch_days,
            "transit_days": row.transit_days,
        }
    path = Path(os.environ.get("PROCESS_PATH", DEFAULT_PROCESS_PATH))
    doc = json.loads(path.read_text(encoding="utf-8"))
    return {k: doc.get(k, 0) for k in (
        "dye_slots", "dye_days", "second_shift_multiplier", "dispatch_days", "transit_days"
    )}  # fmt: skip


def load_process(session: Session) -> tuple[Process, dict[str, StepMeta], dict, list[str]]:
    """Route, machine groups, downtime and settings from the DB (D-43)."""
    settings = production_settings(session)
    step_rows = session.scalars(
        select(ProcessStep).order_by(ProcessStep.sequence, ProcessStep.id)
    ).all()
    machines = session.scalars(select(Machine).order_by(Machine.step, Machine.id)).all()
    by_step: dict[str, list[Machine]] = {}
    for m in machines:
        by_step.setdefault(m.step, []).append(m)

    problems: list[str] = []
    if not step_rows:
        problems.append("The production route has no steps. Add a step in Production plan.")
    metas: dict[str, StepMeta] = {}
    specs: list[StepSpec] = []
    for s in step_rows:
        metas[s.id] = StepMeta(s.id, s.name, s.unit, s.sequence, s.second_shift)
        groups = tuple(
            MachineGroup(m.id, m.count, m.rate_per_machine_day) for m in by_step.get(s.id, [])
        )
        if not groups or sum(g.count * g.rate_per_machine_day for g in groups) <= 0:
            problems.append(f"{s.name} has no working machines, so no order can pass it.")
        specs.append(StepSpec(s.id, groups, s.second_shift))

    downtime = tuple(
        Downtime(d.step, d.from_date, d.to_date, d.machines_down, d.machine_id)
        for d in session.scalars(select(DowntimeRow).order_by(DowntimeRow.from_date))
    )
    process = Process(
        steps=tuple(specs),
        dye_slots=tuple(sorted(date.fromisoformat(d).toordinal() for d in settings["dye_slots"])),
        dye_days=int(settings["dye_days"]),
        second_shift_multiplier=float(settings["second_shift_multiplier"]),
        dispatch_days=int(settings["dispatch_days"]),
        planned_downtime=downtime,
    )
    return process, metas, settings, problems


def latest_yarn_promise(session: Session, order_id: str) -> tuple[Promise, Message] | None:
    rows = session.execute(
        select(Promise, Message)
        .join(Message, Promise.message_id == Message.id)
        .where(
            Promise.order_id == order_id,
            Promise.status == "open",
            Promise.promised_date.is_not(None),
        )
        .order_by(Message.received_at.desc(), Promise.id.desc())
    ).all()
    for promise, message in rows:
        if is_yarn_material(promise.material_id):
            return promise, message
    return None


def run_plan(session: Session, flags: Flags | None = None, use_promises: bool = True) -> PlanRun:
    flags = flags or Flags()
    process, metas, settings, problems = load_process(session)
    for dt in flags.extra_downtime:
        if dt.step not in metas:
            problems.append(f"What-if downtime names an unknown step: {dt.step}.")
    rows = session.scalars(
        select(OrderRow).where(OrderRow.status != "done").order_by(OrderRow.priority, OrderRow.id)
    ).all()

    inputs: dict[str, Order] = {}
    yarn: dict[str, YarnSource] = {}
    unplanned: list[str] = []
    for row in rows:
        source = YarnSource(row.yarn_arrival, "planned") if row.yarn_arrival else None
        if use_promises:
            hit = latest_yarn_promise(session, row.id)
            if hit:
                promise, _ = hit
                source = YarnSource(
                    promise.promised_date, "promise", promise.id, promise.quote, promise.supplier_id
                )
        if source is None:
            unplanned.append(row.id)
            continue
        yarn[row.id] = source
        inputs[row.id] = Order(
            id=row.id,
            pairs=row.pairs,
            ship_by=row.ship_by,
            yarn_arrival=source.date,
            status=row.status,
        )

    schedules: dict[str, OrderSchedule] = {}
    if problems:
        unplanned = [r.id for r in rows]
        inputs = {}
    else:
        schedules = compute_plan(list(inputs.values()), process, flags)
        for oid, sched in schedules.items():
            if not sched.complete:
                problems.append(f"{oid} cannot finish within 400 days with the current machines.")

    return PlanRun(
        process=process,
        flags=flags,
        steps=metas,
        machines={m.id: m for m in session.scalars(select(Machine))},
        downtime_rows=list(session.scalars(select(DowntimeRow).order_by(DowntimeRow.from_date))),
        orders={r.id: r for r in rows},
        inputs=inputs,
        yarn=yarn,
        schedules=schedules,
        unplanned=unplanned,
        transit_days=int(settings.get("transit_days") or 0),
        problems=problems,
    )


def verdict(schedule: OrderSchedule) -> Verdict:
    s = schedule.slack_days
    ready, ship_by = fmt(schedule.ready), fmt(schedule.ship_by)
    if s >= 2:
        return Verdict(
            "ok", "Feasible", f"Ready to ship {ready}, {plural(s)} before the {ship_by} deadline."
        )
    if s >= 0:
        tail = "on the day of the deadline." if s == 0 else f"{plural(s)} before the deadline."
        return Verdict("warn", "Feasible, tight", f"Ready to ship {ready}, {tail}")
    return Verdict(
        "risk",
        f"Not feasible by {ship_by}",
        f"Earliest ready date is {ready}, {plural(-s)} after the deadline.",
    )


def _without_downtime(process: Process) -> Process:
    return Process(
        steps=process.steps,
        dye_slots=process.dye_slots,
        dye_days=process.dye_days,
        second_shift_multiplier=process.second_shift_multiplier,
        dispatch_days=process.dispatch_days,
        planned_downtime=(),
    )


_ANY_DAY = date(2000, 1, 1).toordinal()


def nominal_capacity(process: Process, step_id: str, flags: Flags) -> int:
    """Capacity a day with every machine up (shift setting applied)."""
    bare = Flags(second_shift=flags.second_shift)
    return capacity(_without_downtime(process), step_id, _ANY_DAY, bare)


def nominal_group_capacity(process: Process, step_id: str, machine_id: str, flags: Flags) -> float:
    bare = Flags(second_shift=flags.second_shift)
    return group_capacities(_without_downtime(process), step_id, _ANY_DAY, bare).get(machine_id, 0)


def capacity_series(run: PlanRun, step_id: str, start: date, end: date) -> list[int]:
    days = (end - start).days + 1
    return [
        capacity(run.process, step_id, (start + timedelta(days=i)).toordinal(), run.flags)
        for i in range(days)
    ]


def _all_downtime(run: PlanRun) -> tuple[Downtime, ...]:
    return (*run.process.planned_downtime, *run.flags.extra_downtime)


def risk_notes(run: PlanRun, order_id: str) -> list[str]:
    """Plain-language notes derived from the plan numbers."""
    sched = run.schedules[order_id]
    notes: list[str] = []
    if sched.slack_days < 0:
        late = plural(-sched.slack_days)
        notes.append(f"Misses the {fmt(sched.ship_by)} ship-by date by {late}.")
    elif sched.slack_days <= 1:
        notes.append("Almost no buffer. One day of slip anywhere moves the ship date.")

    yarn = run.yarn[order_id]
    if yarn.source == "promise":
        notes.append(f'Yarn date {fmt(yarn.date)} comes from a supplier promise: "{yarn.quote}".')

    for dt in _all_downtime(run):
        step = sched.steps.get(dt.step)
        if step is None or not (step.start <= dt.to_date and step.finish >= dt.from_date):
            continue
        nominal = nominal_capacity(run.process, dt.step, run.flags)
        reduced = capacity(run.process, dt.step, dt.from_ord, run.flags)
        who = (
            run.machines[dt.machine_id].name
            if dt.machine_id in run.machines
            else (run.step_name(dt.step))
        )
        notes.append(
            f"{who} loses {dt.machines_down} from {fmt(dt.from_date)} to {fmt(dt.to_date)}, "
            f"so {run.step_name(dt.step).lower()} drops from {nominal:,} to {reduced:,} a day."
        )

    first = run.process.step_ids[0]
    mine = sched.steps[first]
    for other_id, other in run.schedules.items():
        if other_id == order_id:
            continue
        shared = sorted(set(mine.usage) & set(other.steps[first].usage))
        if shared:
            span = fmt(shared[0]) + (f" to {fmt(shared[-1])}" if len(shared) > 1 else "")
            notes.append(f"Shares {run.step_name(first).lower()} with {other_id} on {span}.")
    return notes


# ---------- machines: which orders, how loaded, downtime (D-44) ----------


@dataclass
class OrderOnMachine:
    order_id: str
    pairs: float
    start: date
    finish: date


@dataclass
class DowntimeImpact:
    row: DowntimeRow
    lost: float  # units of output lost over the window
    affected_orders: list[str]


@dataclass
class MachineLoad:
    machine: Machine
    step: StepMeta | None
    nominal: float  # a day, every machine up
    available: float  # over the horizon, after downtime
    used: float  # over the horizon
    peak_day: date | None
    peak_utilisation: float
    daily: list[tuple[date, float, float]]  # (day, capacity, used)
    orders: list[OrderOnMachine]
    downtime: list[DowntimeImpact]

    @property
    def utilisation(self) -> float:
        return self.used / self.available if self.available else 0.0


def machine_loads(run: PlanRun) -> list[MachineLoad]:
    horizon = run.horizon
    days: list[date] = []
    if horizon:
        days = [horizon[0] + timedelta(days=i) for i in range((horizon[1] - horizon[0]).days + 1)]

    # Each order's work on each machine group, per day.
    shares: dict[str, dict[str, dict[date, float]]] = {}  # machine -> order -> day -> units
    for oid, sched in run.schedules.items():
        for sid, step in sched.steps.items():
            for gid, per_day in split_by_group(run.process, sid, step.usage, run.flags).items():
                shares.setdefault(gid, {})[oid] = per_day

    out: list[MachineLoad] = []
    order_key = {sid: i for i, sid in enumerate(run.process.step_ids)}
    for m in sorted(run.machines.values(), key=lambda m: (order_key.get(m.step, 99), m.name)):
        meta = run.steps.get(m.step)
        in_route = m.step in order_key
        mine = shares.get(m.id, {})
        daily = []
        for d in days:
            cap = (
                group_capacities(run.process, m.step, d.toordinal(), run.flags).get(m.id, 0)
                if in_route
                else 0
            )
            used = sum(per_day.get(d, 0) for per_day in mine.values())
            daily.append((d, cap, used))
        peak = max(daily, key=lambda x: (x[2] / x[1]) if x[1] else 0, default=None)
        orders = [
            OrderOnMachine(oid, sum(per_day.values()), min(per_day), max(per_day))
            for oid, per_day in mine.items()
            if per_day
        ]
        orders.sort(key=lambda o: o.start)

        impacts = []
        rate = m.rate_per_machine_day * (
            run.process.second_shift_multiplier if run.flags.second_shift and meta and
            meta.second_shift else 1
        )  # fmt: skip
        for row in run.downtime_rows:
            if row.machine_id != m.id:
                continue
            span = (row.to_date - row.from_date).days + 1
            affected = sorted(
                oid
                for oid, per_day in mine.items()
                if any(row.from_date <= d <= row.to_date for d in per_day)
            )
            impacts.append(
                DowntimeImpact(row, min(row.machines_down, m.count) * rate * span, affected)
            )

        out.append(
            MachineLoad(
                machine=m,
                step=meta,
                nominal=nominal_group_capacity(run.process, m.step, m.id, run.flags)
                if in_route
                else 0,
                available=sum(c for _, c, _ in daily),
                used=sum(u for _, _, u in daily),
                peak_day=peak[0] if peak and peak[1] else None,
                peak_utilisation=(peak[2] / peak[1]) if peak and peak[1] else 0.0,
                daily=daily,
                orders=orders,
                downtime=impacts,
            )
        )
    return out


def bottleneck_step(run: PlanRun) -> str | None:
    if not run.process.steps:
        return None
    return min(run.process.step_ids, key=lambda s: nominal_capacity(run.process, s, run.flags))


# ---------- materials ----------


@dataclass(frozen=True)
class NettingRow:
    material_id: str
    name: str
    unit: str
    supplier_id: str | None
    supplier_name: str | None
    on_hand: float
    moq: float
    expected_arrival: date | None
    netting: Netting


def order_netting(session: Session, order: OrderRow) -> list[NettingRow]:
    rows = session.execute(
        select(OrderMaterial, Material, Supplier)
        .join(Material, OrderMaterial.material_id == Material.id)
        .outerjoin(Supplier, Material.supplier_id == Supplier.id)
        .where(OrderMaterial.order_id == order.id)
        .order_by(Material.id)
    ).all()
    out = []
    for om, mat, sup in rows:
        out.append(
            NettingRow(
                material_id=mat.id,
                name=mat.name,
                unit=mat.unit,
                supplier_id=sup.id if sup else None,
                supplier_name=sup.name if sup else None,
                on_hand=mat.on_hand,
                moq=mat.moq,
                expected_arrival=om.expected_arrival,
                netting=net_material(
                    pairs=order.pairs,
                    qty_per_pair=om.qty_per_pair,
                    waste_pct=om.waste_pct or 0,
                    on_hand=mat.on_hand,
                    moq=mat.moq,
                ),
            )
        )
    return out
