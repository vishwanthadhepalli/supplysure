"""Resolve date expressions in a verified quote, in code, against the message's received date.

The model never produces dates. More than one candidate date means ambiguity
(`needs_clarification`), never a guess. Rules are recorded in DECISIONS.md (D-26).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta

_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}  # fmt: skip
_MONTH = (
    r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?"
    r"|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
)
_WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")

_ISO = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_DMY = re.compile(r"\b(\d{1,2})/(\d{1,2})(?:/(\d{2}|\d{4}))?\b")  # day/month, Indian order
_DAY_MONTH = re.compile(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+)?{_MONTH}\b", re.I)
_MONTH_DAY = re.compile(rf"\b{_MONTH}\s+(\d{{1,2}})(?:st|nd|rd|th)?\b", re.I)
_DAY_AFTER_TOMORROW = re.compile(r"\bday after tomorrow\b", re.I)
_TOMORROW = re.compile(r"\btomorrow\b", re.I)
_TODAY = re.compile(r"\btoday\b", re.I)
_IN_DAYS = re.compile(r"\b(?:in|within)\s+(\d{1,3})\s+days?\b", re.I)
_NEXT_WEEK = re.compile(r"\bnext\s+week\b", re.I)
_END_OF_MONTH = re.compile(rf"\b(?:the\s+)?end\s+of\s+(?:{_MONTH}|(this|the|next)\s+month)\b", re.I)
_WEEKDAY = re.compile(rf"\b(?:next\s+|this\s+|by\s+)?({'|'.join(_WEEKDAYS)})\b", re.I)
_ORDINAL_DAY = re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)\b", re.I)


@dataclass(frozen=True)
class DateResolution:
    candidates: tuple[date, ...]

    @property
    def resolved(self) -> date | None:
        return self.candidates[0] if len(self.candidates) == 1 else None

    @property
    def ambiguous(self) -> bool:
        return len(self.candidates) > 1


def _safe_date(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _next_month_day(month: int, day: int, ref: date) -> date | None:
    """Next occurrence of month/day on or after ref."""
    for year in (ref.year, ref.year + 1):
        d = _safe_date(year, month, day)
        if d and d >= ref:
            return d
    return None


def _next_day_of_month(day: int, ref: date) -> date | None:
    """Next date with this day-of-month on or after ref (rolls into later months)."""
    year, month = ref.year, ref.month
    for _ in range(13):
        d = _safe_date(year, month, day)
        if d and d >= ref:
            return d
        month += 1
        if month > 12:
            year, month = year + 1, 1
    return None


def _month_end(year: int, month: int) -> date:
    nxt = date(year + (month == 12), month % 12 + 1, 1)
    return nxt - timedelta(days=1)


def _end_of(m: re.Match, ref: date) -> date:
    """ "end of August" = Aug 31 (next one on or after ref); "end of this/the month" = this
    month's last day; "end of next month" = next month's last day (D-61)."""
    if m.group(1):
        month = _MONTHS[m.group(1)[:3].lower()]
        year = ref.year if _month_end(ref.year, month) >= ref else ref.year + 1
        return _month_end(year, month)
    if m.group(2).lower() == "next":
        return _month_end(ref.year + (ref.month == 12), ref.month % 12 + 1)
    return _month_end(ref.year, ref.month)


def _mask(text: str, match: re.Match) -> str:
    start, end = match.span()
    return text[:start] + " " * (end - start) + text[end:]


def resolve_dates(text: str, reference: date) -> DateResolution:
    found: list[date] = []
    work = text

    def take(pattern: re.Pattern, to_date) -> None:
        nonlocal work
        for m in list(pattern.finditer(work)):
            d = to_date(m)
            if d is not None:
                found.append(d)
            work = _mask(work, m)

    take(_ISO, lambda m: _safe_date(int(m[1]), int(m[2]), int(m[3])))
    take(_END_OF_MONTH, lambda m: _end_of(m, reference))  # before "August" alone is read

    def dmy(m: re.Match) -> date | None:
        day, month = int(m[1]), int(m[2])
        if m[3]:
            year = int(m[3]) + (2000 if len(m[3]) == 2 else 0)
            return _safe_date(year, month, day)
        return _next_month_day(month, day, reference)

    take(_DMY, dmy)
    take(_DAY_MONTH, lambda m: _next_month_day(_MONTHS[m[2][:3].lower()], int(m[1]), reference))
    take(_MONTH_DAY, lambda m: _next_month_day(_MONTHS[m[1][:3].lower()], int(m[2]), reference))
    take(_DAY_AFTER_TOMORROW, lambda m: reference + timedelta(days=2))
    take(_TOMORROW, lambda m: reference + timedelta(days=1))
    take(_TODAY, lambda m: reference)
    take(_IN_DAYS, lambda m: reference + timedelta(days=int(m[1])))
    # "next week" = Saturday of next week (Mon-Sat working week), a conservative reading.
    take(_NEXT_WEEK, lambda m: reference + timedelta(days=7 - reference.weekday() + 5))

    def weekday(m: re.Match) -> date:
        target = _WEEKDAYS.index(m[1].lower())
        ahead = (target - reference.weekday()) % 7 or 7  # same weekday means next week's
        return reference + timedelta(days=ahead)

    take(_WEEKDAY, weekday)
    take(_ORDINAL_DAY, lambda m: _next_day_of_month(int(m[1]), reference))

    unique = tuple(sorted(set(found)))
    return DateResolution(candidates=unique)
