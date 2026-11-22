"""Code-side screen for instruction-like supplier text (prompt injection).

Runs before the model. A hit means: no extraction, no promises, a `suspicious_message`
log entry for the owner to review. The model can also flag a message as suspicious.
"""

from __future__ import annotations

import re

_PATTERNS = [
    r"\b(ignore|disregard|forget)\b.{0,30}\b(previous|prior|above|earlier)\b.{0,20}"
    r"\b(instructions?|prompts?|messages?|rules?)\b",
    r"\bsystem prompt\b",
    r"\byou are now\b",
    r"\bmark\b.{0,20}\b(every|all)\b.{0,20}\b(orders?|promises?)\b",
    r"\bsend\b.{0,40}\b(bank|password|otp|api key|credentials?)\b",
]
_HOSTILE = [re.compile(p, re.I | re.S) for p in _PATTERNS]


def looks_hostile(text: str | None) -> bool:
    return bool(text) and any(p.search(text) for p in _HOSTILE)
