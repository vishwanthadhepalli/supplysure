"""H1 Intake: store a supplier message once, extract promises, verify, store or flag.

Flow (ARCHITECTURE.md "Intake flow"):
  duplicate check -> injection screen -> LLM extraction (no tools) -> per promise:
  quote check, material check, quantity check, date resolution in code -> promise row.
The message and its promises commit in one transaction, unique on (source, source_message_id),
so a redelivered message can never create a second promise.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.audit import log_action, utcnow
from app.intake.dates import resolve_dates
from app.intake.safety import looks_hostile
from app.intake.verifier import quantity_in_quote, quote_in_sources
from app.models import Material, Message, OrderMaterial, Promise, Supplier
from app.providers.llm import ExtractedPromise, ExtractionContext, LLMClient

ACTOR = "system"


class IncomingMessage(BaseModel):
    source: str  # discord, email, web
    source_message_id: str
    supplier_id: str
    received_at: datetime
    kind: Literal["text", "voice", "document"] = "text"
    raw_text: str | None = None
    transcript: str | None = None
    transcript_lang: str | None = None
    english_text: str | None = None
    attachment_url: str | None = None


class Rejection(BaseModel):
    quote: str
    reason: str  # quote_not_in_source, unknown_material


class IngestResult(BaseModel):
    message_id: str
    duplicate: bool = False
    suspicious: bool = False
    promise_ids: list[str] = Field(default_factory=list)
    rejected: list[Rejection] = Field(default_factory=list)


class UnknownSupplierError(ValueError):
    pass


def message_id_for(source: str, source_message_id: str) -> str:
    return f"msg-{source}-{source_message_id}"


def _find_message(session: Session, source: str, source_message_id: str) -> Message | None:
    return session.scalar(
        select(Message).where(
            Message.source == source, Message.source_message_id == source_message_id
        )
    )


def _default_question(quote: str) -> str:
    return f'Could you confirm the exact date for: "{quote}"?'


def _link_order(session: Session, material_id: str) -> str | None:
    """Link to an order only when exactly one order needs this material."""
    order_ids = session.scalars(
        select(OrderMaterial.order_id).where(OrderMaterial.material_id == material_id)
    ).all()
    return order_ids[0] if len(order_ids) == 1 else None


def _build_promise(
    session: Session,
    message: Message,
    index: int,
    extracted: ExtractedPromise,
    sources: list[str | None],
    allowed_materials: set[str],
    now: datetime,
) -> Promise | Rejection:
    if not quote_in_sources(extracted.quote, sources):
        return Rejection(quote=extracted.quote, reason="quote_not_in_source")
    if extracted.material not in allowed_materials:
        return Rejection(quote=extracted.quote, reason="unknown_material")

    quantity = extracted.quantity
    if quantity is not None and not quantity_in_quote(quantity, extracted.quote):
        log_action(
            session,
            ACTOR,
            "quantity_dropped_not_in_quote",
            {"message_id": message.id, "quantity": quantity, "quote": extracted.quote},
            at=now,
        )
        quantity = None

    # Dates come from the verified quote, resolved in code (CLAUDE.md rule 1).
    dates = resolve_dates(extracted.quote, message.received_at.date())
    if extracted.ambiguous or dates.resolved is None:
        status, promised_date = "needs_clarification", None
        question = extracted.clarification_question or _default_question(extracted.quote)
    else:
        status, promised_date, question = "open", dates.resolved, None

    return Promise(
        id=f"{message.id}-p{index}",
        message_id=message.id,
        supplier_id=message.supplier_id,
        order_id=_link_order(session, extracted.material),
        material_id=extracted.material,
        quantity=quantity,
        promised_date=promised_date,
        status=status,
        confidence=extracted.confidence,
        quote=extracted.quote,
        quote_verified=True,
        clarification_question=question,
        created_at=now,
    )


def ingest_message(
    session: Session,
    incoming: IncomingMessage,
    llm: LLMClient,
    now: datetime | None = None,
) -> IngestResult:
    now = now or utcnow()
    msg_id = message_id_for(incoming.source, incoming.source_message_id)

    existing = _find_message(session, incoming.source, incoming.source_message_id)
    if existing is not None:
        return _duplicate(session, existing.id, incoming, now)

    supplier = session.get(Supplier, incoming.supplier_id)
    if supplier is None:
        raise UnknownSupplierError(incoming.supplier_id)

    message = Message(
        id=msg_id,
        source=incoming.source,
        source_message_id=incoming.source_message_id,
        supplier_id=supplier.id,
        received_at=incoming.received_at,
        kind=incoming.kind,
        raw_text=incoming.raw_text,
        transcript=incoming.transcript,
        transcript_lang=incoming.transcript_lang,
        english_text=incoming.english_text,
        attachment_url=incoming.attachment_url,
    )
    session.add(message)
    log_action(session, ACTOR, "message_received", {"message_id": msg_id}, at=now)

    result = IngestResult(message_id=msg_id)
    sources = [incoming.raw_text, incoming.transcript, incoming.english_text]
    model_text = incoming.english_text or incoming.raw_text or incoming.transcript

    if any(looks_hostile(s) for s in sources):
        result.suspicious = True
        log_action(
            session, ACTOR, "suspicious_message", {"message_id": msg_id, "by": "screen"}, at=now
        )
    elif model_text:
        materials = sorted(
            session.scalars(select(Material.id).where(Material.supplier_id == supplier.id))
        )
        context = ExtractionContext(
            supplier_id=supplier.id, supplier_name=supplier.name, materials=materials
        )
        try:
            extraction = llm.extract_promises(model_text, context)
        except Exception:
            # Store nothing, so a redelivery of this message is processed, not seen as a duplicate.
            session.rollback()
            raise
        if extraction.suspicious:
            result.suspicious = True
            log_action(
                session, ACTOR, "suspicious_message", {"message_id": msg_id, "by": "model"}, at=now
            )
        else:
            for index, extracted in enumerate(extraction.promises):
                outcome = _build_promise(
                    session, message, index, extracted, sources, set(materials), now
                )
                if isinstance(outcome, Rejection):
                    result.rejected.append(outcome)
                    log_action(
                        session,
                        ACTOR,
                        "promise_rejected",
                        {"message_id": msg_id, **outcome.model_dump()},
                        at=now,
                    )
                    continue
                session.add(outcome)
                result.promise_ids.append(outcome.id)
                log_action(
                    session,
                    ACTOR,
                    "promise_created",
                    {"promise_id": outcome.id, "status": outcome.status},
                    at=now,
                )

    try:
        session.commit()
    except IntegrityError:
        # Lost a race with a concurrent delivery of the same message.
        session.rollback()
        return _duplicate(session, msg_id, incoming, now)
    return result


def _duplicate(
    session: Session, message_id: str, incoming: IncomingMessage, now: datetime
) -> IngestResult:
    log_action(
        session,
        ACTOR,
        "duplicate_message_ignored",
        {"source": incoming.source, "source_message_id": incoming.source_message_id},
        at=now,
    )
    session.commit()
    return IngestResult(message_id=message_id, duplicate=True)
