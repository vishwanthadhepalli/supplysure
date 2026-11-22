"""Replay data/sample/messages.json through the real intake pipeline.

Uses a FakeLLM that returns only what a model may return (material, quantity, verbatim
quote, clarification question). Dates, status, quote checks, injection screening and
idempotency all run through the production code path. Used by the evals and to put demo
data in the ledger while the real model is undecided (D-03).

Usage: python -m scripts.sample_messages   (after python -m scripts.seed)
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from sqlalchemy.orm import Session

from app.db import SessionLocal, create_schema
from app.intake.pipeline import IncomingMessage, IngestResult, ingest_message
from app.providers.llm import ExtractedPromise, ExtractionResult, FakeLLM

MESSAGES_PATH = Path(__file__).resolve().parents[1] / "data" / "sample" / "messages.json"


def load_messages(path: Path = MESSAGES_PATH) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))["messages"]


def fixture_text(m: dict) -> str:
    return m.get("text") or m.get("transcript")


def incoming(m: dict) -> IncomingMessage:
    return IncomingMessage(
        source="discord",
        source_message_id=m.get("duplicate_of") or m["id"],
        supplier_id=m["supplier"],
        received_at=datetime.fromisoformat(m["received_at"]),
        kind=m["kind"],
        raw_text=m.get("text"),
        transcript=m.get("transcript"),
        transcript_lang=m.get("transcript_lang"),
    )


def fake_response(m: dict) -> ExtractionResult:
    """No dates and no status: the code must derive those."""
    expect = m["expect"]
    return ExtractionResult(
        suspicious=expect.get("suspicious", False),
        promises=[
            ExtractedPromise(
                material=p["material"],
                quantity=p.get("quantity"),
                quote=p["quote"],
                clarification_question=expect.get("clarification_question"),
                confidence=0.9,
            )
            for p in expect["promises"]
        ],
    )


def fixture_llm(messages: list[dict]) -> FakeLLM:
    return FakeLLM(
        {fixture_text(m): fake_response(m) for m in messages if not m.get("duplicate_of")}
    )


def replay(session: Session, messages: list[dict] | None = None) -> list[IngestResult]:
    messages = messages if messages is not None else load_messages()
    llm = fixture_llm(messages)
    return [ingest_message(session, incoming(m), llm) for m in messages]


def main() -> None:
    create_schema()
    with SessionLocal() as session:
        for result in replay(session):
            flags = [k for k in ("duplicate", "suspicious") if getattr(result, k)]
            print(result.message_id, result.promise_ids, flags or "")


if __name__ == "__main__":
    main()
