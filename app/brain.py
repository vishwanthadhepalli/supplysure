"""Factory Brain: a graph derived from the tables (DATA_MODEL "Graph node types").

Nodes are rows (suppliers, materials, machine groups, process steps, orders, buyers,
documents, risk events, downtime); edges come from foreign keys plus the `graph_edges`
table. Traversal is plain BFS.
Node ids are "<type>:<row id>".
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Document, GraphEdge, Material, OrderMaterial, Promise, RiskEvent, Supplier
from app.planning import (
    PlanRun,
    bottleneck_step,
    fmt,
    is_yarn_material,
    machine_loads,
    nominal_capacity,
    verdict,
)

TYPE_LABELS = {
    "event": "Risk event",
    "supplier": "Supplier",
    "material": "Material",
    "machine": "Machine",
    "step": "Process step",
    "order": "Order",
    "document": "Document",
    "customer": "Customer",
}
COLUMNS = ["event", "supplier", "material", "machine", "step", "order", "document", "customer"]


@dataclass
class Node:
    id: str
    type: str
    label: str
    sub: str = ""
    status: str | None = None  # ok, warn, risk
    attrs: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Edge:
    src: str
    dst: str
    rel: str


@dataclass
class Graph:
    nodes: dict[str, Node]
    edges: list[Edge]

    def neighbours(self, node_id: str, direction: str) -> list[str]:
        if direction == "down":
            return [e.dst for e in self.edges if e.src == node_id]
        return [e.src for e in self.edges if e.dst == node_id]


def trace(graph: Graph, start: str, direction: str) -> list[str]:
    """Every node reachable from `start`, downstream ("down") or upstream ("up"), BFS order."""
    if direction not in ("up", "down"):
        raise ValueError(direction)
    if start not in graph.nodes:
        raise KeyError(start)
    seen = {start}
    order: list[str] = []
    queue = deque([start])
    while queue:
        current = queue.popleft()
        for nxt in graph.neighbours(current, direction):
            if nxt not in seen:
                seen.add(nxt)
                order.append(nxt)
                queue.append(nxt)
    return order


def single_source_materials(graph: Graph) -> list[str]:
    return [
        n.id
        for n in graph.nodes.values()
        if n.type == "material" and n.attrs.get("Single source") == "Yes"
    ]


def build_graph(session: Session, run: PlanRun) -> Graph:
    nodes: dict[str, Node] = {}
    edges: list[Edge] = []

    def add_edge(src: str, dst: str, rel: str) -> None:
        if src in nodes and dst in nodes:
            edges.append(Edge(src, dst, rel))

    suppliers = {s.id: s for s in session.scalars(select(Supplier).order_by(Supplier.id))}
    materials = list(session.scalars(select(Material).order_by(Material.id)))
    promises = list(session.scalars(select(Promise)))

    # Suppliers. Status: warn if a promise needs clarification, risk if a yarn promise is later
    # than the order's planned yarn arrival.
    for s in suppliers.values():
        mine = [p for p in promises if p.supplier_id == s.id]
        status = None
        if any(p.status == "needs_clarification" for p in mine):
            status = "warn"
        for p in mine:
            planned = run.orders.get(p.order_id).yarn_arrival if p.order_id in run.orders else None
            if (
                p.status == "open"
                and p.promised_date
                and planned
                and is_yarn_material(p.material_id)
                and p.promised_date > planned
            ):
                status = "risk"
        nodes[f"supplier:{s.id}"] = Node(
            id=f"supplier:{s.id}",
            type="supplier",
            label=s.name,
            sub=f"Tier 1, {s.kind}",
            status=status,
            attrs={
                "Kind": s.kind,
                "Language": s.language,
                "Usual slip": f"{s.usual_slip_days:g} days",
                "Single source": "Yes" if s.single_source else "No",
                "Discord channel": s.discord_channel_id or "None",
                "Promises": str(len(mine)),
            },
        )

    for m in materials:
        sup = suppliers.get(m.supplier_id)
        single = bool(sup and sup.single_source)
        nodes[f"material:{m.id}"] = Node(
            id=f"material:{m.id}",
            type="material",
            label=m.name,
            sub=f"{m.on_hand:,.0f} {m.unit} on hand",
            status="warn" if single else None,
            attrs={
                "Unit": m.unit,
                "On hand": f"{m.on_hand:,.0f} {m.unit}",
                "MOQ": f"{m.moq:,.0f} {m.unit}",
                "Supplier": sup.name if sup else "None",
                "Single source": "Yes" if single else "No",
            },
        )
        add_edge(f"supplier:{m.supplier_id}", f"material:{m.id}", "supplies")

    # Production route: machine groups work on steps; steps run in route order (D-46).
    loads = {m.machine.id: m for m in machine_loads(run)}
    bottleneck = bottleneck_step(run)
    for meta in run.steps.values():
        groups = [m for m in loads.values() if m.machine.step == meta.id]
        nominal = nominal_capacity(run.process, meta.id, run.flags) if groups else 0
        count = sum(g.machine.count for g in groups)
        nodes[f"step:{meta.id}"] = Node(
            id=f"step:{meta.id}",
            type="step",
            label=meta.name,
            sub=f"{nominal:,} a day" + (" · limit" if meta.id == bottleneck else ""),
            status="risk" if not groups else ("warn" if meta.id == bottleneck else None),
            attrs={
                "Position in route": str(meta.sequence),
                "Machine groups": str(len(groups)),
                meta.unit.capitalize(): str(count),
                "Capacity": f"{nominal:,} pairs a day",
                "Second shift": "Yes" if meta.second_shift else "No",
                "Limit of the plant": "Yes" if meta.id == bottleneck else "No",
            },
        )
    for load in loads.values():
        m, meta = load.machine, load.step
        unit = meta.unit if meta else "machines"
        busy = load.utilisation
        status = "risk" if busy >= 0.95 else ("warn" if busy >= 0.85 or load.downtime else None)
        nodes[f"machine:{m.id}"] = Node(
            id=f"machine:{m.id}",
            type="machine",
            label=m.name,
            sub=f"{busy:.0%} busy · {m.count} {unit}",
            status=status,
            attrs={
                "Step": meta.name if meta else m.step,
                unit.capitalize(): str(m.count),
                "Rate": f"{m.rate_per_machine_day:,} a day each",
                "Capacity": f"{load.nominal:,.0f} a day",
                "Load over the plan": f"{busy:.0%}",
                "Orders": ", ".join(o.order_id for o in load.orders) or "None",
                "Downtime windows": str(len(load.downtime)),
            },
        )
        add_edge(f"machine:{m.id}", f"step:{m.step}", "works on")
    route = run.process.step_ids
    for a, b in zip(route, route[1:], strict=False):
        add_edge(f"step:{a}", f"step:{b}", "next")

    for d in run.downtime_rows:
        nid = f"event:{d.id}"
        target = f"machine:{d.machine_id}" if d.machine_id else f"step:{d.step}"
        hits = next(
            (i.affected_orders for i in loads[d.machine_id].downtime if i.row.id == d.id), []
        ) if d.machine_id in loads else []  # fmt: skip
        nodes[nid] = Node(
            id=nid,
            type="event",
            label=(d.reason or ("Maintenance" if d.planned else "Probable breakdown"))[:24],
            sub=f"{fmt(d.from_date)} to {fmt(d.to_date)}",
            status="risk" if hits and not d.planned else "warn",
            attrs={
                "Kind": "Planned downtime" if d.planned else "Probable breakdown",
                "Effect": f"{d.machines_down} out on {nodes[target].label}"
                if target in nodes
                else f"{d.machines_down} out",
                "From": fmt(d.from_date),
                "To": fmt(d.to_date),
                "Orders running then": ", ".join(hits) or "None",
            },
        )
        add_edge(nid, target, "affects")
    for i, d in enumerate(run.flags.extra_downtime):
        nid = f"event:whatif-{i}"
        nodes[nid] = Node(
            id=nid,
            type="event",
            label="What-if downtime",
            sub=f"{fmt(d.from_date)} to {fmt(d.to_date)}",
            status="risk",
            attrs={
                "Kind": "Scenario",
                "Effect": f"{d.machines_down} out on {run.step_name(d.step)}",
            },
        )
        add_edge(nid, f"step:{d.step}", "affects")

    for r in session.scalars(select(RiskEvent)):
        nid = f"event:{r.id}"
        nodes[nid] = Node(
            id=nid,
            type="event",
            label=r.kind.capitalize(),
            sub=r.description[:40],
            status="warn",
            attrs={"Kind": r.kind, "Description": r.description},
        )
        add_edge(nid, f"supplier:{r.supplier_id}", "affects")

    buyers: dict[str, list[str]] = {}
    for oid, row in run.orders.items():
        sched = run.schedules.get(oid)
        attrs = {
            "Buyer": row.buyer,
            "Quantity": f"{row.pairs:,} pairs",
            "Ship by": fmt(row.ship_by),
            "Status": row.status,
        }
        status, sub = None, row.status
        if sched:
            v = verdict(sched)
            status = v.level
            sign = "+" if sched.slack_days >= 0 else ""
            sub = f"Ready {fmt(sched.ready)} · {sign}{sched.slack_days} d"
            first = sched.steps[run.process.step_ids[0]]
            attrs |= {
                "Ready to ship": fmt(sched.ready),
                "Slack": f"{sched.slack_days} days",
                run.step_name(
                    run.process.step_ids[0]
                ): f"{fmt(first.start)} to {fmt(first.finish)}",
            }
        nodes[f"order:{oid}"] = Node(f"order:{oid}", "order", oid, sub, status, attrs)
        buyers.setdefault(row.buyer, []).append(oid)
        if run.process.step_ids:
            add_edge(f"step:{run.process.step_ids[-1]}", f"order:{oid}", "makes")

    for om in session.scalars(select(OrderMaterial)):
        add_edge(f"material:{om.material_id}", f"order:{om.order_id}", "needed by")

    for doc in session.scalars(select(Document)):
        nid = f"document:{doc.id}"
        nodes[nid] = Node(nid, "document", doc.doc_type.replace("_", " ").capitalize(), doc.id)
        add_edge(f"supplier:{doc.supplier_id}", nid, "sent")
        add_edge(nid, f"order:{doc.order_id}", "about")

    for buyer, oids in buyers.items():
        nid = f"customer:{buyer}"
        joined = ", ".join(oids)
        nodes[nid] = Node(nid, "customer", buyer, joined, attrs={"Orders": joined})
        for oid in oids:
            add_edge(f"order:{oid}", nid, "for")

    for ge in session.scalars(select(GraphEdge)):
        add_edge(f"{ge.src_type}:{ge.src_id}", f"{ge.dst_type}:{ge.dst_id}", ge.rel)

    return Graph(nodes=nodes, edges=edges)
