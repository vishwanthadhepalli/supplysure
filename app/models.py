"""ORM models mirroring docs/DATA_MODEL.md. Ids are text keys supplied by the caller."""

from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class Supplier(Base):
    __tablename__ = "suppliers"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    # yarn, dye, trim, print, freight, other
    kind: Mapped[str] = mapped_column(String, nullable=False)
    language: Mapped[str] = mapped_column(String, nullable=False, default="en")
    discord_channel_id: Mapped[str | None] = mapped_column(String)
    usual_slip_days: Mapped[float] = mapped_column(Numeric(5, 2, asdecimal=False), default=0)
    single_source: Mapped[bool] = mapped_column(Boolean, default=False)


class Material(Base):
    __tablename__ = "materials"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    unit: Mapped[str] = mapped_column(String, nullable=False)
    moq: Mapped[float] = mapped_column(Numeric(12, 2, asdecimal=False), default=1)
    on_hand: Mapped[float] = mapped_column(Numeric(12, 2, asdecimal=False), default=0)
    supplier_id: Mapped[str | None] = mapped_column(ForeignKey("suppliers.id"))


class ProcessStep(Base):
    """One in-house step of the production route, in `sequence` order (D-43)."""

    __tablename__ = "process_steps"

    id: Mapped[str] = mapped_column(String, primary_key=True)  # knit, link, ...
    name: Mapped[str] = mapped_column(String, nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    unit: Mapped[str] = mapped_column(String, nullable=False, default="machines")
    second_shift: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class Machine(Base):
    """A group of identical machines (or people, lines, stations) doing one step."""

    __tablename__ = "machines"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False, default="")
    step: Mapped[str] = mapped_column(ForeignKey("process_steps.id"), nullable=False)
    count: Mapped[int] = mapped_column(Integer, nullable=False)
    rate_per_machine_day: Mapped[int] = mapped_column(Integer, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)


class Downtime(Base):
    __tablename__ = "downtime"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    step: Mapped[str] = mapped_column(String, nullable=False)
    machine_id: Mapped[str | None] = mapped_column(ForeignKey("machines.id"))
    from_date: Mapped[date] = mapped_column(Date, nullable=False)
    to_date: Mapped[date] = mapped_column(Date, nullable=False)
    machines_down: Mapped[int] = mapped_column(Integer, nullable=False)
    planned: Mapped[bool] = mapped_column(Boolean, default=True)  # False: probable breakdown
    reason: Mapped[str | None] = mapped_column(Text)


class ProductionSettings(Base):
    """Outside stages and shift rules. Single row, id = 1."""

    __tablename__ = "production_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    dye_slots: Mapped[str] = mapped_column(Text, nullable=False)  # JSON list of ISO dates
    dye_days: Mapped[int] = mapped_column(Integer, nullable=False)
    second_shift_multiplier: Mapped[float] = mapped_column(
        Numeric(4, 2, asdecimal=False), nullable=False
    )
    dispatch_days: Mapped[int] = mapped_column(Integer, nullable=False)
    transit_days: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    buyer: Mapped[str] = mapped_column(String, nullable=False)
    pairs: Mapped[int] = mapped_column(Integer, nullable=False)
    ship_by: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default="booked")
    grams_per_pair: Mapped[float] = mapped_column(Numeric(6, 2, asdecimal=False), nullable=False)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Planned yarn arrival (planner input). An open promise for the order's yarn overrides it.
    yarn_arrival: Mapped[date | None] = mapped_column(Date)


class OrderMaterial(Base):
    __tablename__ = "order_materials"

    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), primary_key=True)
    material_id: Mapped[str] = mapped_column(ForeignKey("materials.id"), primary_key=True)
    qty_per_pair: Mapped[float] = mapped_column(Numeric(12, 6, asdecimal=False), nullable=False)
    waste_pct: Mapped[float] = mapped_column(Numeric(5, 2, asdecimal=False), default=0)
    expected_arrival: Mapped[date | None] = mapped_column(Date)


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (UniqueConstraint("source", "source_message_id"),)

    id: Mapped[str] = mapped_column(String, primary_key=True)
    source: Mapped[str] = mapped_column(String, nullable=False)  # discord, email, web
    source_message_id: Mapped[str] = mapped_column(String, nullable=False)
    supplier_id: Mapped[str | None] = mapped_column(ForeignKey("suppliers.id"))
    received_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    kind: Mapped[str] = mapped_column(String, nullable=False)  # text, voice, document
    raw_text: Mapped[str | None] = mapped_column(Text)
    transcript: Mapped[str | None] = mapped_column(Text)
    transcript_lang: Mapped[str | None] = mapped_column(String)
    english_text: Mapped[str | None] = mapped_column(Text)
    attachment_url: Mapped[str | None] = mapped_column(String)


class Promise(Base):
    __tablename__ = "promises"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    message_id: Mapped[str] = mapped_column(ForeignKey("messages.id"), nullable=False)
    supplier_id: Mapped[str] = mapped_column(ForeignKey("suppliers.id"), nullable=False)
    order_id: Mapped[str | None] = mapped_column(ForeignKey("orders.id"))
    material_id: Mapped[str | None] = mapped_column(ForeignKey("materials.id"))
    quantity: Mapped[float | None] = mapped_column(Numeric(12, 2, asdecimal=False))
    promised_date: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String, nullable=False, default="open")
    confidence: Mapped[float] = mapped_column(Numeric(3, 2, asdecimal=False), default=0.5)
    quote: Mapped[str] = mapped_column(Text, nullable=False)
    quote_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    clarification_question: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime)


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    message_id: Mapped[str | None] = mapped_column(ForeignKey("messages.id"))
    supplier_id: Mapped[str | None] = mapped_column(ForeignKey("suppliers.id"))
    order_id: Mapped[str | None] = mapped_column(ForeignKey("orders.id"))
    doc_type: Mapped[str] = mapped_column(String, nullable=False)
    extracted_json: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class RiskEvent(Base):
    __tablename__ = "risk_events"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    kind: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    starts_on: Mapped[date | None] = mapped_column(Date)
    ends_on: Mapped[date | None] = mapped_column(Date)
    supplier_id: Mapped[str | None] = mapped_column(ForeignKey("suppliers.id"))


class GraphEdge(Base):
    __tablename__ = "graph_edges"

    src_type: Mapped[str] = mapped_column(String, primary_key=True)
    src_id: Mapped[str] = mapped_column(String, primary_key=True)
    dst_type: Mapped[str] = mapped_column(String, primary_key=True)
    dst_id: Mapped[str] = mapped_column(String, primary_key=True)
    # supplies, needs, runs_on, ships_to, affects
    rel: Mapped[str] = mapped_column(String, primary_key=True)


class Reminder(Base):
    __tablename__ = "reminders"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    promise_id: Mapped[str] = mapped_column(ForeignKey("promises.id"), nullable=False)
    # 1 message, 2 follow-up, 3 call, 4 alert owner
    step: Mapped[int] = mapped_column(Integer, nullable=False)
    draft_text: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default="drafted")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime)


class ActionLog(Base):
    __tablename__ = "action_log"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    actor: Mapped[str] = mapped_column(String, nullable=False)  # system, owner, agent
    action: Mapped[str] = mapped_column(String, nullable=False)
    payload: Mapped[str | None] = mapped_column(Text)


class Prediction(Base):
    __tablename__ = "predictions"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), nullable=False)
    made_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    predicted_ready: Mapped[date] = mapped_column(Date, nullable=False)
    actual_ready: Mapped[date | None] = mapped_column(Date)


class AssistantInput(Base):
    """One thing the owner said or typed to SupplyGPT (D-50)."""

    __tablename__ = "assistant_inputs"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    kind: Mapped[str] = mapped_column(String, nullable=False)  # text, voice
    language_code: Mapped[str | None] = mapped_column(String)
    original_text: Mapped[str] = mapped_column(Text, nullable=False)  # typed text or transcript
    english_text: Mapped[str] = mapped_column(Text, nullable=False)  # what the model read
    reply: Mapped[str | None] = mapped_column(Text)  # English
    reply_local: Mapped[str | None] = mapped_column(Text)  # in the owner's language, if not English


class AssistantProposal(Base):
    """A change SupplyGPT proposes. Nothing happens until the owner applies it."""

    __tablename__ = "assistant_proposals"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    input_id: Mapped[str] = mapped_column(ForeignKey("assistant_inputs.id"), nullable=False)
    type: Mapped[str] = mapped_column(String, nullable=False)
    # pending, needs_clarification, invalid, applying, applied, rejected
    status: Mapped[str] = mapped_column(String, nullable=False, default="pending")
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    quote: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[str] = mapped_column(Text, nullable=False)  # JSON, validated
    reason: Mapped[str | None] = mapped_column(Text)  # why invalid / question to ask
    result_ref: Mapped[str | None] = mapped_column(String)  # id of what was created or changed
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime)
