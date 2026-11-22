"""Checks that model output is grounded in the source text."""

from __future__ import annotations

import re
from collections.abc import Iterable

_THOUSANDS_SEP = re.compile(r"(?<=\d),(?=\d)")
_NUMBER = re.compile(r"\d+(?:\.\d+)?")
_WORD_NUMBERS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13,
    "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
    "nineteen": 19, "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60,
    "seventy": 70, "eighty": 80, "ninety": 90, "a couple of": 2, "a pair of": 2,
    "single": 1, "a dozen": 12, "dozen": 12,
}  # fmt: skip
_SCALE = {"hundred": 100, "thousand": 1_000, "lakh": 100_000, "lakhs": 100_000}


def quote_in_sources(quote: str, sources: Iterable[str | None]) -> bool:
    """Exact, case-sensitive substring match. Zero tolerance (EVALS.md)."""
    if not quote or not quote.strip():
        return False
    return any(source and quote in source for source in sources)


def find_verbatim(quote: str, source: str) -> str | None:
    """The quote as it appears in source: exact match, else a case-insensitive match returned
    with the source's own casing (still verbatim). None if it is not there."""
    if not quote or not quote.strip() or not source:
        return None
    if quote in source:
        return quote
    i = source.lower().find(quote.lower())
    return source[i : i + len(quote)] if i >= 0 else None


def numbers_in(text: str) -> set[float]:
    """Numbers written as digits ("45,000", "2.5", "2k") or simple words ("four", "two hundred")."""
    clean = _THOUSANDS_SEP.sub("", text.lower())
    found = {float(n) for n in _NUMBER.findall(clean)}
    found |= {float(m) * 1000 for m in re.findall(r"(\d+(?:\.\d+)?)\s*k\b", clean)}
    for m in re.finditer(r"(\d+(?:\.\d+)?)\s*(hundred|thousand|lakhs?)\b", clean):
        found.add(float(m.group(1)) * _SCALE[m.group(2)])
    for word, value in _WORD_NUMBERS.items():
        for m in re.finditer(rf"\b{word}\b(?:\s+(hundred|thousand|lakhs?))?", clean):
            found.add(float(value) * (_SCALE[m.group(1)] if m.group(1) else 1))
    return found


def quantity_in_quote(quantity: float, quote: str) -> bool:
    """The stated quantity must appear as a number in the quote ("45,000" matches 45000)."""
    return float(quantity) in numbers_in(quote)
