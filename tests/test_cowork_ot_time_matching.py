"""An overtime shift finds its coworkers by the hours actually worked.

The rotation shift the overtime replaced says nothing about when it is worked:
giving up a vacation day to cover a night shift keeps N2 as original_shift while
the person is on the floor 22:00-06:30 with the N3 crew.
"""

import datetime
from types import SimpleNamespace

from app.core.schedule.cowork import get_coworkers_for_day


def _dt(day: int, hour: int, minute: int = 0):
    return datetime.datetime(2026, 8, day, hour, minute)


def _person(pid, name, code, start=None, end=None, original=None):
    return {
        "person_id": pid,
        "person_name": name,
        "shift": SimpleNamespace(code=code),
        "original_shift": SimpleNamespace(code=original) if original else None,
        "start": start,
        "end": end,
    }


# Kalle works overtime 22:00-06:30 on a day his rotation had him on N2 14:00-22:30.
KALLE = _person(6, "Kalle", "OT", _dt(26, 22), _dt(27, 6, 30), original="N2")
ADRIANA = _person(2, "Adriana", "N3", _dt(26, 22), _dt(27, 6, 30))
PETER = _person(10, "Peter", "N2", _dt(26, 14), _dt(26, 22, 30))
OKAN = _person(8, "Okan", "N1", _dt(26, 6), _dt(26, 14, 30))
DAY = [KALLE, ADRIANA, PETER, OKAN]


def test_overtime_matches_the_crew_it_actually_works_with():
    coworkers = get_coworkers_for_day(6, "OT", DAY, KALLE["start"], KALLE["end"])
    assert coworkers == ["Adriana"]


def test_overtime_is_not_a_coworker_of_the_shift_it_replaced():
    coworkers = get_coworkers_for_day(10, "N2", DAY, PETER["start"], PETER["end"])
    assert coworkers == []


def test_regular_shift_still_matches_on_shift_code():
    day = [*DAY, _person(9, "Pierre", "N1", _dt(26, 6), _dt(26, 14, 30))]
    assert get_coworkers_for_day(8, "N1", day, OKAN["start"], OKAN["end"]) == ["Pierre"]
