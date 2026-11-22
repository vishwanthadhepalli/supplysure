"""Action log helpers. Every state change and every refusal is logged."""

import json
import uuid
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.models import ActionLog


def utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def log_action(
    session: Session,
    actor: str,
    action: str,
    payload: dict | None = None,
    at: datetime | None = None,
) -> ActionLog:
    """Add an action_log row to the session. The caller commits."""
    row = ActionLog(
        id=f"act-{uuid.uuid4().hex}",
        at=at or utcnow(),
        actor=actor,
        action=action,
        payload=json.dumps(payload, default=str, sort_keys=True) if payload else None,
    )
    session.add(row)
    return row
