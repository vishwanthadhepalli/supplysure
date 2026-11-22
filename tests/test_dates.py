from datetime import date

import pytest

from app.intake.dates import resolve_dates

SAT_JUN_12 = date(2027, 6, 12)  # a Saturday


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("ready by Friday", date(2027, 6, 18)),
        ("dispatch on Saturday", date(2027, 6, 19)),  # same weekday means next week
        ("reach on the 22nd", date(2027, 6, 22)),
        ("reach on the 5th", date(2027, 7, 5)),  # day already passed this month
        ("tomorrow", date(2027, 6, 13)),
        ("day after tomorrow", date(2027, 6, 14)),
        ("in 3 days", date(2027, 6, 15)),
        ("next week", date(2027, 6, 19)),  # Saturday of next week
        ("by 22 June", date(2027, 6, 22)),
        ("by June 22nd", date(2027, 6, 22)),
        ("on 2 July", date(2027, 7, 2)),
        ("on 2027-07-01", date(2027, 7, 1)),
        ("on 25/06", date(2027, 6, 25)),
    ],
)
def test_single_date(text, expected):
    result = resolve_dates(text, SAT_JUN_12)
    assert result.resolved == expected
    assert not result.ambiguous


def test_alternatives_are_ambiguous():
    result = resolve_dates("Cartons dispatch on 12th or 22nd", date(2027, 6, 13))
    assert result.ambiguous
    assert result.resolved is None
    assert result.candidates == (date(2027, 6, 22), date(2027, 7, 12))


def test_numbers_are_not_dates():
    assert resolve_dates("Labels 45,000 pcs ready", SAT_JUN_12).candidates == ()


def test_same_date_twice_is_not_ambiguous():
    assert resolve_dates("22nd June, yes the 22nd", SAT_JUN_12).resolved == date(2027, 6, 22)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("ship by end of August", date(2027, 8, 31)),
        ("by the end of June", date(2027, 6, 30)),
        ("end of this month", date(2027, 6, 30)),
        ("by end of the month", date(2027, 6, 30)),
        ("end of next month", date(2027, 7, 31)),
        ("end of May", date(2028, 5, 31)),  # May 2027 has passed
        ("end of February", date(2028, 2, 29)),  # leap year
    ],
)
def test_end_of_month(text, expected):
    assert resolve_dates(text, SAT_JUN_12).resolved == expected


def test_mid_month_stays_unclear():
    assert resolve_dates("mid August", SAT_JUN_12).candidates == ()  # a month alone has no day
    assert resolve_dates("in August", SAT_JUN_12).candidates == ()
