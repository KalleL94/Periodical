"""Tests for week-based vacation counting in scheduled work days (audit item B4).

Decision: a week of vacation consumes only the days the employee was actually scheduled to
work (all 7 weekdays minus OFF-shift days), consistent with day-level vacation. Without a
known schedule it falls back to a flat Mon-Fri.
"""

import datetime

from app.core.schedule.vacation import _count_weekdays_in_vacation_weeks

YEAR = 2025
WEEK = 10
START = datetime.date(YEAR, 1, 1)
END = datetime.date(YEAR, 12, 31)


def _week_days(week: int) -> list[datetime.date]:
    return [datetime.date.fromisocalendar(YEAR, week, i) for i in range(1, 8)]


def test_without_off_dates_falls_back_to_mon_fri():
    # No schedule known -> flat Monday..Friday = 5 days.
    assert _count_weekdays_in_vacation_weeks([WEEK], YEAR, START, END, None) == 5


def test_with_schedule_counts_only_worked_days():
    days = _week_days(WEEK)
    # Scheduled OFF on Mon, Sat, Sun -> 4 scheduled work days remain (incl. a worked weekend day).
    off = {days[0], days[5], days[6]}
    assert _count_weekdays_in_vacation_weeks([WEEK], YEAR, START, END, off) == 4


def test_weekend_work_day_is_counted():
    days = _week_days(WEEK)
    # Off the whole standard week except a worked Sunday -> only that Sunday counts.
    off = set(days) - {days[6]}  # everything off except Sunday
    assert _count_weekdays_in_vacation_weeks([WEEK], YEAR, START, END, off) == 1


def test_empty_off_set_counts_all_seven():
    # An explicit empty set means "schedule known, no OFF days" -> all 7 days worked.
    assert _count_weekdays_in_vacation_weeks([WEEK], YEAR, START, END, set()) == 7
