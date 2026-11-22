"""Deterministic plant planner. Pure functions: no I/O, no database.

Port of `cap`, `slotFor`, `sched` and `computeAll` in reference/prototypes/plant-planner.html,
generalised (D-42) so the owner can define the production route:
- steps run in the order given by `Process.steps` (any number, any ids);
- a step can have several machine groups, each with its own count and rate;
- downtime can hit one machine group or a whole step;
- second shift applies to steps marked `second_shift`.
With one group per step and the sample step order, results are identical to the prototype
(the golden tests check this).

Days are integer ordinals internally (date.toordinal()); the public results use dates.
Rules are in docs/PLANNER_SPEC.md. The LLM never calls these for dates; it only reads results.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date

# The sample route, kept for reference and backwards compatibility. The planner itself uses
# the order of Process.steps.
STEP_ORDER: tuple[str, ...] = ("knit", "link", "board", "pair", "pack")
KNIT = "knit"


def _round_half_up(x: float) -> int:
    # JavaScript Math.round semantics, which the prototype uses.
    return math.floor(x + 0.5)


@dataclass(frozen=True)
class MachineGroup:
    id: str
    count: int
    rate_per_machine_day: int


@dataclass(frozen=True)
class StepSpec:
    id: str
    groups: tuple[MachineGroup, ...]
    second_shift: bool = False  # does the second-shift what-if apply to this step?

    @classmethod
    def single(cls, step_id: str, machines: int, rate: int, second_shift: bool | None = None):
        """One machine group for the whole step (the sample data's shape)."""
        return cls(
            id=step_id,
            groups=(MachineGroup(step_id, machines, rate),),
            second_shift=step_id == KNIT if second_shift is None else second_shift,
        )

    @property
    def machines(self) -> int:
        return sum(g.count for g in self.groups)


@dataclass(frozen=True)
class Downtime:
    step: str
    from_date: date
    to_date: date
    machines_down: int
    machine_id: str | None = None  # None: take machines from the step's groups in order

    @property
    def from_ord(self) -> int:
        return self.from_date.toordinal()

    @property
    def to_ord(self) -> int:
        return self.to_date.toordinal()


@dataclass(frozen=True)
class Process:
    steps: tuple[StepSpec, ...]
    dye_slots: tuple[int, ...]  # ordinals, sorted ascending
    dye_days: int
    second_shift_multiplier: float
    dispatch_days: int
    planned_downtime: tuple[Downtime, ...] = ()

    def step(self, step_id: str) -> StepSpec:
        for s in self.steps:
            if s.id == step_id:
                return s
        raise KeyError(step_id)

    @property
    def step_ids(self) -> tuple[str, ...]:
        return tuple(s.id for s in self.steps)

    @classmethod
    def from_dict(cls, data: dict) -> Process:
        return cls(
            steps=tuple(
                StepSpec.single(
                    s["id"],
                    int(s["machines"]),
                    int(s["rate_per_machine_day"]),
                    s.get("second_shift"),
                )
                for s in data["steps"]
            ),
            dye_slots=tuple(sorted(date.fromisoformat(d).toordinal() for d in data["dye_slots"])),
            dye_days=int(data["dye_days"]),
            second_shift_multiplier=float(data["second_shift_multiplier"]),
            dispatch_days=int(data["dispatch_days"]),
            planned_downtime=tuple(downtime_from_dict(d) for d in data.get("planned_downtime", [])),
        )


def downtime_from_dict(d: dict) -> Downtime:
    return Downtime(
        step=d["step"],
        from_date=date.fromisoformat(d["from"]),
        to_date=date.fromisoformat(d["to"]),
        machines_down=int(d["machines_down"]),
        machine_id=d.get("machine_id"),
    )


@dataclass(frozen=True)
class Order:
    id: str
    pairs: int
    ship_by: date
    yarn_arrival: date
    status: str = "booked"  # booked or tentative


@dataclass(frozen=True)
class Flags:
    second_shift: bool = False
    new_request_ahead: bool = False  # moves tentative orders ahead of the second booked order
    yarn_delay_days: int = 0
    extra_downtime: tuple[Downtime, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class StepSchedule:
    start: date
    finish: date
    days: int
    usage: dict[date, int]


@dataclass(frozen=True)
class OrderSchedule:
    order_id: str
    dye_start: date
    dye_finish: date
    steps: dict[str, StepSchedule]  # in route order
    ready: date
    ship_by: date
    slack_days: int  # ship_by minus ready; negative means late
    complete: bool = True  # False if a step could not finish within the loop guard


def group_capacities(process: Process, step_id: str, day: int, flags: Flags) -> dict[str, float]:
    """Capacity of each machine group of a step on `day` (ordinal), after downtime and shift.

    Not rounded: used to split a step's work between its groups for display.
    """
    spec = process.step(step_id)
    up = {g.id: g.count for g in spec.groups}
    for dt in (*process.planned_downtime, *flags.extra_downtime):
        if dt.step != step_id or not (dt.from_ord <= day <= dt.to_ord):
            continue
        if dt.machine_id is not None:
            if dt.machine_id in up:
                up[dt.machine_id] = max(0, up[dt.machine_id] - dt.machines_down)
            continue
        left = dt.machines_down  # step-wide: take machines from the groups in order
        for g in spec.groups:
            take = min(left, up[g.id])
            up[g.id] -= take
            left -= take
    multiplier = process.second_shift_multiplier if flags.second_shift and spec.second_shift else 1
    return {g.id: up[g.id] * g.rate_per_machine_day * multiplier for g in spec.groups}


def capacity(process: Process, step_id: str, day: int, flags: Flags) -> int:
    """Pairs (or units) a step can process on `day` (ordinal), after downtime."""
    return _round_half_up(sum(group_capacities(process, step_id, day, flags).values()))


def dye_start_for(process: Process, yarn_arrival: int) -> int:
    """First dye slot on or after yarn arrival + 1 day (ordinal). Falls back to that day."""
    earliest = yarn_arrival + 1
    for slot in process.dye_slots:
        if slot >= earliest:
            return slot
    return earliest


def priority_sequence(orders: Sequence[Order], new_request_ahead: bool) -> list[Order]:
    """Booked orders first, in input order, then tentative ones.

    With new_request_ahead, tentative orders move in front of the second booked order.
    """
    booked = [o for o in orders if o.status != "tentative"]
    tentative = [o for o in orders if o.status == "tentative"]
    if new_request_ahead and len(booked) >= 2 and tentative:
        return [booked[0], *tentative, *booked[1:]]
    return [*booked, *tentative]


@dataclass
class _RawStep:
    start: int
    finish: int
    usage: dict[int, int]
    complete: bool


def _sum_upto(usage: dict[int, int], upto: int) -> int:
    return sum(v for k, v in usage.items() if k <= upto)


GUARD_DAYS = 400


def _schedule_order(
    order: Order,
    process: Process,
    flags: Flags,
    committed: dict[str, dict[int, int]],
) -> OrderSchedule:
    yarn = order.yarn_arrival.toordinal() + flags.yarn_delay_days
    dye_start = dye_start_for(process, yarn)
    dye_finish = dye_start + process.dye_days - 1
    first_start = dye_finish + 1

    raw: dict[str, _RawStep] = {}
    prev: str | None = None
    for i, step_id in enumerate(process.step_ids):
        usage: dict[int, int] = {}
        remaining = order.pairs
        done = 0
        day = first_start if i == 0 else raw[prev].start + 1  # type: ignore[index]
        first: int | None = None
        last: int | None = None
        guard = 0
        while remaining > 0 and guard < GUARD_DAYS:
            guard += 1
            free = capacity(process, step_id, day, flags) - committed[step_id].get(day, 0)
            if i == 0:
                upstream = math.inf
            else:
                upstream = _sum_upto(raw[prev].usage, day - 1) - done  # type: ignore[index]
            use = max(0, min(free, upstream, remaining))
            if use > 0:
                usage[day] = use
                done += use
                remaining -= use
                if first is None:
                    first = day
                last = day
            day += 1
        if first is None:
            first = day
            last = day
        for k, v in usage.items():
            committed[step_id][k] = committed[step_id].get(k, 0) + v
        raw[step_id] = _RawStep(first, last, usage, remaining == 0)  # type: ignore[arg-type]
        prev = step_id

    last_finish = raw[process.step_ids[-1]].finish
    ready = last_finish + process.dispatch_days
    ship_by = order.ship_by.toordinal()
    steps = {
        sid: StepSchedule(
            start=date.fromordinal(r.start),
            finish=date.fromordinal(r.finish),
            days=r.finish - r.start + 1,
            usage={date.fromordinal(k): v for k, v in sorted(r.usage.items())},
        )
        for sid, r in raw.items()
    }
    return OrderSchedule(
        order_id=order.id,
        dye_start=date.fromordinal(dye_start),
        dye_finish=date.fromordinal(dye_finish),
        steps=steps,
        ready=date.fromordinal(ready),
        ship_by=order.ship_by,
        slack_days=ship_by - ready,
        complete=all(r.complete for r in raw.values()),
    )


def compute_plan(
    orders: Iterable[Order], process: Process, flags: Flags | None = None
) -> dict[str, OrderSchedule]:
    """Schedule every order in priority order, sharing capacity between them."""
    flags = flags or Flags()
    if not process.steps:
        raise ValueError("the production route has no steps")
    committed: dict[str, dict[int, int]] = {sid: {} for sid in process.step_ids}
    result: dict[str, OrderSchedule] = {}
    for order in priority_sequence(list(orders), flags.new_request_ahead):
        result[order.id] = _schedule_order(order, process, flags, committed)
    return result


def split_by_group(
    process: Process, step_id: str, usage: dict[date, int], flags: Flags
) -> dict[str, dict[date, float]]:
    """Attribute a step's daily work to its machine groups, in proportion to each group's
    capacity that day (D-44). Display only; the schedule is decided per step."""
    out: dict[str, dict[date, float]] = {g.id: {} for g in process.step(step_id).groups}
    for day, pairs in usage.items():
        caps = group_capacities(process, step_id, day.toordinal(), flags)
        total = sum(caps.values())
        for gid, cap in caps.items():
            if total > 0 and cap > 0:
                out[gid][day] = pairs * cap / total
    return out
