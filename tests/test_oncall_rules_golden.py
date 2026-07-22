"""Golden master for build_oncall_rules_for_year.

This function generates the on-call (jour/beredskap) compensation rules that drive
real money: 192 kr/h for storhelg, 112 kr/h for a red day, 97/75 kr/h otherwise.
The golden master pins every field of every rule, in emission order, so that a
refactor or a rate change has to show exactly what moved.

Years chosen to exercise the moveable feasts, since every storhelg block except
Christmas and New Year hangs off Easter or Midsummer:
- 2026: reference year (Nationaldagen on a Saturday, Christmas Eve on a Thursday)
- 2027: early Easter (28 Mar), Nationaldagen on a Sunday
- 2028: leap year
- 2035: earliest Easter in the modern range (25 Mar), Kristi himmelsfard lands
        the day after Forsta maj's morning-after rule
- 2038: latest possible Easter (25 Apr), Nationaldagen on a Sunday

To regenerate after an intentional change:
    venv/bin/python3 -m pytest tests/test_oncall_rules_golden.py -q
then run this module directly to print the new block:
    venv/bin/python3 tests/test_oncall_rules_golden.py
and paste the output over GOLDEN below, reviewing every changed line.
"""

import datetime
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

# ruff: noqa: E402
from app.core.oncall import build_oncall_rules_for_year

GOLDEN_YEARS = (2026, 2027, 2028, 2035, 2038)


def _format_rule(rule) -> str:
    """One stable, human-diffable line per rule, with every field the model carries."""
    if rule.specific_dates:
        # Generated rules always carry exactly one date; assert rather than hide it.
        assert len(rule.specific_dates) == 1, rule
        day = datetime.date.fromisoformat(rule.specific_dates[0])
        when = f"{day.isoformat()} {day:%a}"
    else:
        days = ",".join(str(d) for d in (rule.days or []))
        when = f"weekdays[{days}]".ljust(19)
    return (
        f"{when}  {rule.code:<17} {rule.start_time}-{rule.end_time} "
        f"rate={rule.rate} fixed={rule.fixed_hourly_rate} prio={rule.priority} "
        f"spans={rule.spans_to_next_day} gen={rule.generated}"
    )


def _dump(year: int) -> list[str]:
    """Rules for a year as text lines, in emission order (order is load-bearing)."""
    return [f"# {year}"] + [_format_rule(r) for r in build_oncall_rules_for_year(year)]


def _dump_all() -> list[str]:
    lines: list[str] = []
    for year in GOLDEN_YEARS:
        lines.extend(_dump(year))
    return lines


GOLDEN = """\
# 2026
weekdays[0,1,2,3,4]  OC_WEEKDAY        00:00-24:00 rate=None fixed=75 prio=1 spans=False gen=False
weekdays[4]          OC_WEEKEND        17:00-07:00 rate=None fixed=97 prio=2 spans=True gen=False
weekdays[5]          OC_WEEKEND_SAT    00:00-24:00 rate=None fixed=97 prio=2 spans=False gen=False
weekdays[6]          OC_WEEKEND_SUN    00:00-24:00 rate=None fixed=97 prio=2 spans=False gen=False
weekdays[0]          OC_WEEKEND_MON    00:00-07:00 rate=None fixed=97 prio=2 spans=False gen=False
weekdays[]           OC_HOLIDAY_EVE    18:00-07:00 rate=None fixed=97 prio=3 spans=True gen=True
weekdays[]           OC_HOLIDAY        00:00-24:00 rate=None fixed=112 prio=4 spans=False gen=True
weekdays[]           OC_SPECIAL        00:00-24:00 rate=None fixed=192 prio=5 spans=False gen=True
2026-06-05 Fri  OC_NATIONALDAGEN  17:00-00:00 rate=None fixed=112 prio=6 spans=False gen=True
2026-06-06 Sat  OC_NATIONALDAGEN  00:00-00:00 rate=None fixed=112 prio=6 spans=False gen=True
2026-06-07 Sun  OC_NATIONALDAGEN  00:00-00:00 rate=None fixed=112 prio=6 spans=False gen=True
2026-06-08 Mon  OC_NATIONALDAGEN  00:00-07:00 rate=None fixed=112 prio=6 spans=False gen=True
2026-12-23 Wed  OC_SPECIAL        17:00-00:00 rate=None fixed=192 prio=5 spans=False gen=True
2026-06-18 Thu  OC_SPECIAL        17:00-00:00 rate=None fixed=192 prio=5 spans=False gen=True
2026-12-30 Wed  OC_SPECIAL        17:00-00:00 rate=None fixed=192 prio=5 spans=False gen=True
2026-01-01 Thu  OC_SPECIAL        00:00-00:00 rate=None fixed=192 prio=5 spans=False gen=True
2026-04-02 Thu  OC_SPECIAL        17:00-00:00 rate=None fixed=192 prio=5 spans=False gen=True
2026-04-03 Fri  OC_SPECIAL        00:00-00:00 rate=None fixed=192 prio=5 spans=False gen=True
2026-04-04 Sat  OC_SPECIAL        00:00-00:00 rate=None fixed=192 prio=5 spans=False gen=True
2026-04-05 Sun  OC_SPECIAL        00:00-00:00 rate=None fixed=192 prio=5 spans=False gen=True
2026-04-06 Mon  OC_SPECIAL        00:00-00:00 rate=None fixed=192 prio=5 spans=False gen=True
2026-06-19 Fri  OC_SPECIAL        00:00-00:00 rate=None fixed=192 prio=5 spans=False gen=True
2026-06-20 Sat  OC_SPECIAL        00:00-00:00 rate=None fixed=192 prio=5 spans=False gen=True
2026-06-21 Sun  OC_SPECIAL        00:00-00:00 rate=None fixed=192 prio=5 spans=False gen=True
2026-12-24 Thu  OC_SPECIAL        00:00-00:00 rate=None fixed=192 prio=5 spans=False gen=True
2026-12-25 Fri  OC_SPECIAL        00:00-00:00 rate=None fixed=192 prio=5 spans=False gen=True
2026-12-26 Sat  OC_SPECIAL        00:00-00:00 rate=None fixed=192 prio=5 spans=False gen=True
2026-12-27 Sun  OC_SPECIAL        00:00-00:00 rate=None fixed=192 prio=5 spans=False gen=True
2026-12-31 Thu  OC_SPECIAL        00:00-00:00 rate=None fixed=192 prio=5 spans=False gen=True
2027-01-01 Fri  OC_SPECIAL        00:00-00:00 rate=None fixed=192 prio=5 spans=False gen=True
2027-01-02 Sat  OC_SPECIAL        00:00-00:00 rate=None fixed=192 prio=5 spans=False gen=True
2027-01-03 Sun  OC_SPECIAL        00:00-00:00 rate=None fixed=192 prio=5 spans=False gen=True
2026-12-28 Mon  OC_SPECIAL        00:00-07:00 rate=None fixed=192 prio=5 spans=False gen=True
2026-01-02 Fri  OC_SPECIAL        00:00-07:00 rate=None fixed=192 prio=5 spans=False gen=True
2026-04-07 Tue  OC_SPECIAL        00:00-07:00 rate=None fixed=192 prio=5 spans=False gen=True
2026-06-22 Mon  OC_SPECIAL        00:00-07:00 rate=None fixed=192 prio=5 spans=False gen=True
2026-01-06 Tue  OC_HOLIDAY        00:00-24:00 rate=None fixed=112 prio=4 spans=False gen=True
2026-01-05 Mon  OC_HOLIDAY        17:00-24:00 rate=None fixed=112 prio=4 spans=False gen=True
2026-01-07 Wed  OC_HOLIDAY        00:00-07:00 rate=None fixed=112 prio=4 spans=False gen=True
2026-05-01 Fri  OC_HOLIDAY        00:00-24:00 rate=None fixed=112 prio=4 spans=False gen=True
2026-04-30 Thu  OC_HOLIDAY        17:00-24:00 rate=None fixed=112 prio=4 spans=False gen=True
2026-05-02 Sat  OC_HOLIDAY        00:00-24:00 rate=None fixed=112 prio=4 spans=False gen=True
2026-05-03 Sun  OC_HOLIDAY        00:00-24:00 rate=None fixed=112 prio=4 spans=False gen=True
2026-05-04 Mon  OC_HOLIDAY        00:00-07:00 rate=None fixed=112 prio=4 spans=False gen=True
2026-05-14 Thu  OC_HOLIDAY        00:00-24:00 rate=None fixed=112 prio=4 spans=False gen=True
2026-05-13 Wed  OC_HOLIDAY        17:00-24:00 rate=None fixed=112 prio=4 spans=False gen=True
2026-05-15 Fri  OC_HOLIDAY        00:00-07:00 rate=None fixed=112 prio=4 spans=False gen=True
2026-10-31 Sat  OC_HOLIDAY        00:00-24:00 rate=None fixed=112 prio=4 spans=False gen=True
2026-10-30 Fri  OC_HOLIDAY        17:00-24:00 rate=None fixed=112 prio=4 spans=False gen=True
2026-11-01 Sun  OC_HOLIDAY        00:00-24:00 rate=None fixed=112 prio=4 spans=False gen=True
2026-11-02 Mon  OC_HOLIDAY        00:00-07:00 rate=None fixed=112 prio=4 spans=False gen=True
# 2027
weekdays[0,1,2,3,4]  OC_WEEKDAY        00:00-24:00 rate=None fixed=75 prio=1 spans=False gen=False
weekdays[4]          OC_WEEKEND        17:00-07:00 rate=None fixed=97 prio=2 spans=True gen=False
weekdays[5]          OC_WEEKEND_SAT    00:00-24:00 rate=None fixed=97 prio=2 spans=False gen=False
weekdays[6]          OC_WEEKEND_SUN    00:00-24:00 rate=None fixed=97 prio=2 spans=False gen=False
weekdays[0]          OC_WEEKEND_MON    00:00-07:00 rate=None fixed=97 prio=2 spans=False gen=False
weekdays[]           OC_HOLIDAY_EVE    18:00-07:00 rate=None fixed=97 prio=3 spans=True gen=True
weekdays[]           OC_HOLIDAY        00:00-24:00 rate=None fixed=112 prio=4 spans=False gen=True
weekdays[]           OC_SPECIAL        00:00-24:00 rate=None fixed=192 prio=5 spans=False gen=True
2027-06-05 Sat  OC_NATIONALDAGEN  17:00-00:00 rate=None fixed=112 prio=6 spans=False gen=True
2027-06-06 Sun  OC_NATIONALDAGEN  00:00-00:00 rate=None fixed=112 prio=6 spans=False gen=True
2027-06-07 Mon  OC_NATIONALDAGEN  00:00-07:00 rate=None fixed=112 prio=6 spans=False gen=True
2027-12-23 Thu  OC_SPECIAL        17:00-00:00 rate=None fixed=192 prio=5 spans=False gen=True
2027-06-24 Thu  OC_SPECIAL        17:00-00:00 rate=None fixed=192 prio=5 spans=False gen=True
2027-12-30 Thu  OC_SPECIAL        17:00-00:00 rate=None fixed=192 prio=5 spans=False gen=True
2027-01-01 Fri  OC_SPECIAL        00:00-00:00 rate=None fixed=192 prio=5 spans=False gen=True
2027-01-02 Sat  OC_SPECIAL        00:00-00:00 rate=None fixed=192 prio=5 spans=False gen=True
2027-01-03 Sun  OC_SPECIAL        00:00-00:00 rate=None fixed=192 prio=5 spans=False gen=True
2027-03-25 Thu  OC_SPECIAL        17:00-00:00 rate=None fixed=192 prio=5 spans=False gen=True
2027-03-26 Fri  OC_SPECIAL        00:00-00:00 rate=None fixed=192 prio=5 spans=False gen=True
2027-03-27 Sat  OC_SPECIAL        00:00-00:00 rate=None fixed=192 prio=5 spans=False gen=True
2027-03-28 Sun  OC_SPECIAL        00:00-00:00 rate=None fixed=192 prio=5 spans=False gen=True
2027-03-29 Mon  OC_SPECIAL        00:00-00:00 rate=None fixed=192 prio=5 spans=False gen=True
2027-06-25 Fri  OC_SPECIAL        00:00-00:00 rate=None fixed=192 prio=5 spans=False gen=True
2027-06-26 Sat  OC_SPECIAL        00:00-00:00 rate=None fixed=192 prio=5 spans=False gen=True
2027-06-27 Sun  OC_SPECIAL        00:00-00:00 rate=None fixed=192 prio=5 spans=False gen=True
2027-12-24 Fri  OC_SPECIAL        00:00-00:00 rate=None fixed=192 prio=5 spans=False gen=True
2027-12-25 Sat  OC_SPECIAL        00:00-00:00 rate=None fixed=192 prio=5 spans=False gen=True
2027-12-26 Sun  OC_SPECIAL        00:00-00:00 rate=None fixed=192 prio=5 spans=False gen=True
2027-12-31 Fri  OC_SPECIAL        00:00-00:00 rate=None fixed=192 prio=5 spans=False gen=True
2028-01-01 Sat  OC_SPECIAL        00:00-00:00 rate=None fixed=192 prio=5 spans=False gen=True
2028-01-02 Sun  OC_SPECIAL        00:00-00:00 rate=None fixed=192 prio=5 spans=False gen=True
2027-12-27 Mon  OC_SPECIAL        00:00-07:00 rate=None fixed=192 prio=5 spans=False gen=True
2027-01-04 Mon  OC_SPECIAL        00:00-07:00 rate=None fixed=192 prio=5 spans=False gen=True
2027-03-30 Tue  OC_SPECIAL        00:00-07:00 rate=None fixed=192 prio=5 spans=False gen=True
2027-06-28 Mon  OC_SPECIAL        00:00-07:00 rate=None fixed=192 prio=5 spans=False gen=True
2027-01-06 Wed  OC_HOLIDAY        00:00-24:00 rate=None fixed=112 prio=4 spans=False gen=True
2027-01-05 Tue  OC_HOLIDAY        17:00-24:00 rate=None fixed=112 prio=4 spans=False gen=True
2027-01-07 Thu  OC_HOLIDAY        00:00-07:00 rate=None fixed=112 prio=4 spans=False gen=True
2027-05-01 Sat  OC_HOLIDAY        00:00-24:00 rate=None fixed=112 prio=4 spans=False gen=True
2027-04-30 Fri  OC_HOLIDAY        17:00-24:00 rate=None fixed=112 prio=4 spans=False gen=True
2027-05-02 Sun  OC_HOLIDAY        00:00-24:00 rate=None fixed=112 prio=4 spans=False gen=True
2027-05-03 Mon  OC_HOLIDAY        00:00-07:00 rate=None fixed=112 prio=4 spans=False gen=True
2027-05-06 Thu  OC_HOLIDAY        00:00-24:00 rate=None fixed=112 prio=4 spans=False gen=True
2027-05-05 Wed  OC_HOLIDAY        17:00-24:00 rate=None fixed=112 prio=4 spans=False gen=True
2027-05-07 Fri  OC_HOLIDAY        00:00-07:00 rate=None fixed=112 prio=4 spans=False gen=True
2027-11-06 Sat  OC_HOLIDAY        00:00-24:00 rate=None fixed=112 prio=4 spans=False gen=True
2027-11-05 Fri  OC_HOLIDAY        17:00-24:00 rate=None fixed=112 prio=4 spans=False gen=True
2027-11-07 Sun  OC_HOLIDAY        00:00-24:00 rate=None fixed=112 prio=4 spans=False gen=True
2027-11-08 Mon  OC_HOLIDAY        00:00-07:00 rate=None fixed=112 prio=4 spans=False gen=True
# 2028
weekdays[0,1,2,3,4]  OC_WEEKDAY        00:00-24:00 rate=None fixed=75 prio=1 spans=False gen=False
weekdays[4]          OC_WEEKEND        17:00-07:00 rate=None fixed=97 prio=2 spans=True gen=False
weekdays[5]          OC_WEEKEND_SAT    00:00-24:00 rate=None fixed=97 prio=2 spans=False gen=False
weekdays[6]          OC_WEEKEND_SUN    00:00-24:00 rate=None fixed=97 prio=2 spans=False gen=False
weekdays[0]          OC_WEEKEND_MON    00:00-07:00 rate=None fixed=97 prio=2 spans=False gen=False
weekdays[]           OC_HOLIDAY_EVE    18:00-07:00 rate=None fixed=97 prio=3 spans=True gen=True
weekdays[]           OC_HOLIDAY        00:00-24:00 rate=None fixed=112 prio=4 spans=False gen=True
weekdays[]           OC_SPECIAL        00:00-24:00 rate=None fixed=192 prio=5 spans=False gen=True
2028-06-05 Mon  OC_NATIONALDAGEN  17:00-00:00 rate=None fixed=112 prio=6 spans=False gen=True
2028-06-06 Tue  OC_NATIONALDAGEN  00:00-00:00 rate=None fixed=112 prio=6 spans=False gen=True
2028-06-07 Wed  OC_NATIONALDAGEN  00:00-07:00 rate=None fixed=112 prio=6 spans=False gen=True
2028-12-23 Sat  OC_SPECIAL        17:00-00:00 rate=None fixed=192 prio=5 spans=False gen=True
2028-06-22 Thu  OC_SPECIAL        17:00-00:00 rate=None fixed=192 prio=5 spans=False gen=True
2028-12-30 Sat  OC_SPECIAL        17:00-00:00 rate=None fixed=192 prio=5 spans=False gen=True
2028-01-01 Sat  OC_SPECIAL        00:00-00:00 rate=None fixed=192 prio=5 spans=False gen=True
2028-01-02 Sun  OC_SPECIAL        00:00-00:00 rate=None fixed=192 prio=5 spans=False gen=True
2028-04-13 Thu  OC_SPECIAL        17:00-00:00 rate=None fixed=192 prio=5 spans=False gen=True
2028-04-14 Fri  OC_SPECIAL        00:00-00:00 rate=None fixed=192 prio=5 spans=False gen=True
2028-04-15 Sat  OC_SPECIAL        00:00-00:00 rate=None fixed=192 prio=5 spans=False gen=True
2028-04-16 Sun  OC_SPECIAL        00:00-00:00 rate=None fixed=192 prio=5 spans=False gen=True
2028-04-17 Mon  OC_SPECIAL        00:00-00:00 rate=None fixed=192 prio=5 spans=False gen=True
2028-06-23 Fri  OC_SPECIAL        00:00-00:00 rate=None fixed=192 prio=5 spans=False gen=True
2028-06-24 Sat  OC_SPECIAL        00:00-00:00 rate=None fixed=192 prio=5 spans=False gen=True
2028-06-25 Sun  OC_SPECIAL        00:00-00:00 rate=None fixed=192 prio=5 spans=False gen=True
2028-12-24 Sun  OC_SPECIAL        00:00-00:00 rate=None fixed=192 prio=5 spans=False gen=True
2028-12-25 Mon  OC_SPECIAL        00:00-00:00 rate=None fixed=192 prio=5 spans=False gen=True
2028-12-26 Tue  OC_SPECIAL        00:00-00:00 rate=None fixed=192 prio=5 spans=False gen=True
2028-12-31 Sun  OC_SPECIAL        00:00-00:00 rate=None fixed=192 prio=5 spans=False gen=True
2029-01-01 Mon  OC_SPECIAL        00:00-00:00 rate=None fixed=192 prio=5 spans=False gen=True
2028-12-27 Wed  OC_SPECIAL        00:00-07:00 rate=None fixed=192 prio=5 spans=False gen=True
2028-01-03 Mon  OC_SPECIAL        00:00-07:00 rate=None fixed=192 prio=5 spans=False gen=True
2028-04-18 Tue  OC_SPECIAL        00:00-07:00 rate=None fixed=192 prio=5 spans=False gen=True
2028-06-26 Mon  OC_SPECIAL        00:00-07:00 rate=None fixed=192 prio=5 spans=False gen=True
2028-01-06 Thu  OC_HOLIDAY        00:00-24:00 rate=None fixed=112 prio=4 spans=False gen=True
2028-01-05 Wed  OC_HOLIDAY        17:00-24:00 rate=None fixed=112 prio=4 spans=False gen=True
2028-01-07 Fri  OC_HOLIDAY        00:00-07:00 rate=None fixed=112 prio=4 spans=False gen=True
2028-05-01 Mon  OC_HOLIDAY        00:00-24:00 rate=None fixed=112 prio=4 spans=False gen=True
2028-04-30 Sun  OC_HOLIDAY        17:00-24:00 rate=None fixed=112 prio=4 spans=False gen=True
2028-05-02 Tue  OC_HOLIDAY        00:00-07:00 rate=None fixed=112 prio=4 spans=False gen=True
2028-05-25 Thu  OC_HOLIDAY        00:00-24:00 rate=None fixed=112 prio=4 spans=False gen=True
2028-05-24 Wed  OC_HOLIDAY        17:00-24:00 rate=None fixed=112 prio=4 spans=False gen=True
2028-05-26 Fri  OC_HOLIDAY        00:00-07:00 rate=None fixed=112 prio=4 spans=False gen=True
2028-11-04 Sat  OC_HOLIDAY        00:00-24:00 rate=None fixed=112 prio=4 spans=False gen=True
2028-11-03 Fri  OC_HOLIDAY        17:00-24:00 rate=None fixed=112 prio=4 spans=False gen=True
2028-11-05 Sun  OC_HOLIDAY        00:00-24:00 rate=None fixed=112 prio=4 spans=False gen=True
2028-11-06 Mon  OC_HOLIDAY        00:00-07:00 rate=None fixed=112 prio=4 spans=False gen=True
# 2035
weekdays[0,1,2,3,4]  OC_WEEKDAY        00:00-24:00 rate=None fixed=75 prio=1 spans=False gen=False
weekdays[4]          OC_WEEKEND        17:00-07:00 rate=None fixed=97 prio=2 spans=True gen=False
weekdays[5]          OC_WEEKEND_SAT    00:00-24:00 rate=None fixed=97 prio=2 spans=False gen=False
weekdays[6]          OC_WEEKEND_SUN    00:00-24:00 rate=None fixed=97 prio=2 spans=False gen=False
weekdays[0]          OC_WEEKEND_MON    00:00-07:00 rate=None fixed=97 prio=2 spans=False gen=False
weekdays[]           OC_HOLIDAY_EVE    18:00-07:00 rate=None fixed=97 prio=3 spans=True gen=True
weekdays[]           OC_HOLIDAY        00:00-24:00 rate=None fixed=112 prio=4 spans=False gen=True
weekdays[]           OC_SPECIAL        00:00-24:00 rate=None fixed=192 prio=5 spans=False gen=True
2035-06-05 Tue  OC_NATIONALDAGEN  17:00-00:00 rate=None fixed=112 prio=6 spans=False gen=True
2035-06-06 Wed  OC_NATIONALDAGEN  00:00-00:00 rate=None fixed=112 prio=6 spans=False gen=True
2035-06-07 Thu  OC_NATIONALDAGEN  00:00-07:00 rate=None fixed=112 prio=6 spans=False gen=True
2035-12-23 Sun  OC_SPECIAL        17:00-00:00 rate=None fixed=192 prio=5 spans=False gen=True
2035-06-21 Thu  OC_SPECIAL        17:00-00:00 rate=None fixed=192 prio=5 spans=False gen=True
2035-12-30 Sun  OC_SPECIAL        17:00-00:00 rate=None fixed=192 prio=5 spans=False gen=True
2035-01-01 Mon  OC_SPECIAL        00:00-00:00 rate=None fixed=192 prio=5 spans=False gen=True
2035-03-22 Thu  OC_SPECIAL        17:00-00:00 rate=None fixed=192 prio=5 spans=False gen=True
2035-03-23 Fri  OC_SPECIAL        00:00-00:00 rate=None fixed=192 prio=5 spans=False gen=True
2035-03-24 Sat  OC_SPECIAL        00:00-00:00 rate=None fixed=192 prio=5 spans=False gen=True
2035-03-25 Sun  OC_SPECIAL        00:00-00:00 rate=None fixed=192 prio=5 spans=False gen=True
2035-03-26 Mon  OC_SPECIAL        00:00-00:00 rate=None fixed=192 prio=5 spans=False gen=True
2035-06-22 Fri  OC_SPECIAL        00:00-00:00 rate=None fixed=192 prio=5 spans=False gen=True
2035-06-23 Sat  OC_SPECIAL        00:00-00:00 rate=None fixed=192 prio=5 spans=False gen=True
2035-06-24 Sun  OC_SPECIAL        00:00-00:00 rate=None fixed=192 prio=5 spans=False gen=True
2035-12-24 Mon  OC_SPECIAL        00:00-00:00 rate=None fixed=192 prio=5 spans=False gen=True
2035-12-25 Tue  OC_SPECIAL        00:00-00:00 rate=None fixed=192 prio=5 spans=False gen=True
2035-12-26 Wed  OC_SPECIAL        00:00-00:00 rate=None fixed=192 prio=5 spans=False gen=True
2035-12-31 Mon  OC_SPECIAL        00:00-00:00 rate=None fixed=192 prio=5 spans=False gen=True
2036-01-01 Tue  OC_SPECIAL        00:00-00:00 rate=None fixed=192 prio=5 spans=False gen=True
2035-12-27 Thu  OC_SPECIAL        00:00-07:00 rate=None fixed=192 prio=5 spans=False gen=True
2035-01-02 Tue  OC_SPECIAL        00:00-07:00 rate=None fixed=192 prio=5 spans=False gen=True
2035-03-27 Tue  OC_SPECIAL        00:00-07:00 rate=None fixed=192 prio=5 spans=False gen=True
2035-06-25 Mon  OC_SPECIAL        00:00-07:00 rate=None fixed=192 prio=5 spans=False gen=True
2035-01-06 Sat  OC_HOLIDAY        00:00-24:00 rate=None fixed=112 prio=4 spans=False gen=True
2035-01-05 Fri  OC_HOLIDAY        17:00-24:00 rate=None fixed=112 prio=4 spans=False gen=True
2035-01-07 Sun  OC_HOLIDAY        00:00-24:00 rate=None fixed=112 prio=4 spans=False gen=True
2035-01-08 Mon  OC_HOLIDAY        00:00-07:00 rate=None fixed=112 prio=4 spans=False gen=True
2035-05-01 Tue  OC_HOLIDAY        00:00-24:00 rate=None fixed=112 prio=4 spans=False gen=True
2035-04-30 Mon  OC_HOLIDAY        17:00-24:00 rate=None fixed=112 prio=4 spans=False gen=True
2035-05-02 Wed  OC_HOLIDAY        00:00-07:00 rate=None fixed=112 prio=4 spans=False gen=True
2035-05-03 Thu  OC_HOLIDAY        00:00-24:00 rate=None fixed=112 prio=4 spans=False gen=True
2035-05-02 Wed  OC_HOLIDAY        17:00-24:00 rate=None fixed=112 prio=4 spans=False gen=True
2035-05-04 Fri  OC_HOLIDAY        00:00-07:00 rate=None fixed=112 prio=4 spans=False gen=True
2035-11-03 Sat  OC_HOLIDAY        00:00-24:00 rate=None fixed=112 prio=4 spans=False gen=True
2035-11-02 Fri  OC_HOLIDAY        17:00-24:00 rate=None fixed=112 prio=4 spans=False gen=True
2035-11-04 Sun  OC_HOLIDAY        00:00-24:00 rate=None fixed=112 prio=4 spans=False gen=True
2035-11-05 Mon  OC_HOLIDAY        00:00-07:00 rate=None fixed=112 prio=4 spans=False gen=True
# 2038
weekdays[0,1,2,3,4]  OC_WEEKDAY        00:00-24:00 rate=None fixed=75 prio=1 spans=False gen=False
weekdays[4]          OC_WEEKEND        17:00-07:00 rate=None fixed=97 prio=2 spans=True gen=False
weekdays[5]          OC_WEEKEND_SAT    00:00-24:00 rate=None fixed=97 prio=2 spans=False gen=False
weekdays[6]          OC_WEEKEND_SUN    00:00-24:00 rate=None fixed=97 prio=2 spans=False gen=False
weekdays[0]          OC_WEEKEND_MON    00:00-07:00 rate=None fixed=97 prio=2 spans=False gen=False
weekdays[]           OC_HOLIDAY_EVE    18:00-07:00 rate=None fixed=97 prio=3 spans=True gen=True
weekdays[]           OC_HOLIDAY        00:00-24:00 rate=None fixed=112 prio=4 spans=False gen=True
weekdays[]           OC_SPECIAL        00:00-24:00 rate=None fixed=192 prio=5 spans=False gen=True
2038-06-05 Sat  OC_NATIONALDAGEN  17:00-00:00 rate=None fixed=112 prio=6 spans=False gen=True
2038-06-06 Sun  OC_NATIONALDAGEN  00:00-00:00 rate=None fixed=112 prio=6 spans=False gen=True
2038-06-07 Mon  OC_NATIONALDAGEN  00:00-07:00 rate=None fixed=112 prio=6 spans=False gen=True
2038-12-23 Thu  OC_SPECIAL        17:00-00:00 rate=None fixed=192 prio=5 spans=False gen=True
2038-06-24 Thu  OC_SPECIAL        17:00-00:00 rate=None fixed=192 prio=5 spans=False gen=True
2038-12-30 Thu  OC_SPECIAL        17:00-00:00 rate=None fixed=192 prio=5 spans=False gen=True
2038-01-01 Fri  OC_SPECIAL        00:00-00:00 rate=None fixed=192 prio=5 spans=False gen=True
2038-01-02 Sat  OC_SPECIAL        00:00-00:00 rate=None fixed=192 prio=5 spans=False gen=True
2038-01-03 Sun  OC_SPECIAL        00:00-00:00 rate=None fixed=192 prio=5 spans=False gen=True
2038-04-22 Thu  OC_SPECIAL        17:00-00:00 rate=None fixed=192 prio=5 spans=False gen=True
2038-04-23 Fri  OC_SPECIAL        00:00-00:00 rate=None fixed=192 prio=5 spans=False gen=True
2038-04-24 Sat  OC_SPECIAL        00:00-00:00 rate=None fixed=192 prio=5 spans=False gen=True
2038-04-25 Sun  OC_SPECIAL        00:00-00:00 rate=None fixed=192 prio=5 spans=False gen=True
2038-04-26 Mon  OC_SPECIAL        00:00-00:00 rate=None fixed=192 prio=5 spans=False gen=True
2038-06-25 Fri  OC_SPECIAL        00:00-00:00 rate=None fixed=192 prio=5 spans=False gen=True
2038-06-26 Sat  OC_SPECIAL        00:00-00:00 rate=None fixed=192 prio=5 spans=False gen=True
2038-06-27 Sun  OC_SPECIAL        00:00-00:00 rate=None fixed=192 prio=5 spans=False gen=True
2038-12-24 Fri  OC_SPECIAL        00:00-00:00 rate=None fixed=192 prio=5 spans=False gen=True
2038-12-25 Sat  OC_SPECIAL        00:00-00:00 rate=None fixed=192 prio=5 spans=False gen=True
2038-12-26 Sun  OC_SPECIAL        00:00-00:00 rate=None fixed=192 prio=5 spans=False gen=True
2038-12-31 Fri  OC_SPECIAL        00:00-00:00 rate=None fixed=192 prio=5 spans=False gen=True
2039-01-01 Sat  OC_SPECIAL        00:00-00:00 rate=None fixed=192 prio=5 spans=False gen=True
2039-01-02 Sun  OC_SPECIAL        00:00-00:00 rate=None fixed=192 prio=5 spans=False gen=True
2038-12-27 Mon  OC_SPECIAL        00:00-07:00 rate=None fixed=192 prio=5 spans=False gen=True
2038-01-04 Mon  OC_SPECIAL        00:00-07:00 rate=None fixed=192 prio=5 spans=False gen=True
2038-04-27 Tue  OC_SPECIAL        00:00-07:00 rate=None fixed=192 prio=5 spans=False gen=True
2038-06-28 Mon  OC_SPECIAL        00:00-07:00 rate=None fixed=192 prio=5 spans=False gen=True
2038-01-06 Wed  OC_HOLIDAY        00:00-24:00 rate=None fixed=112 prio=4 spans=False gen=True
2038-01-05 Tue  OC_HOLIDAY        17:00-24:00 rate=None fixed=112 prio=4 spans=False gen=True
2038-01-07 Thu  OC_HOLIDAY        00:00-07:00 rate=None fixed=112 prio=4 spans=False gen=True
2038-05-01 Sat  OC_HOLIDAY        00:00-24:00 rate=None fixed=112 prio=4 spans=False gen=True
2038-04-30 Fri  OC_HOLIDAY        17:00-24:00 rate=None fixed=112 prio=4 spans=False gen=True
2038-05-02 Sun  OC_HOLIDAY        00:00-24:00 rate=None fixed=112 prio=4 spans=False gen=True
2038-05-03 Mon  OC_HOLIDAY        00:00-07:00 rate=None fixed=112 prio=4 spans=False gen=True
2038-06-03 Thu  OC_HOLIDAY        00:00-24:00 rate=None fixed=112 prio=4 spans=False gen=True
2038-06-02 Wed  OC_HOLIDAY        17:00-24:00 rate=None fixed=112 prio=4 spans=False gen=True
2038-06-04 Fri  OC_HOLIDAY        00:00-07:00 rate=None fixed=112 prio=4 spans=False gen=True
2038-11-06 Sat  OC_HOLIDAY        00:00-24:00 rate=None fixed=112 prio=4 spans=False gen=True
2038-11-05 Fri  OC_HOLIDAY        17:00-24:00 rate=None fixed=112 prio=4 spans=False gen=True
2038-11-07 Sun  OC_HOLIDAY        00:00-24:00 rate=None fixed=112 prio=4 spans=False gen=True
2038-11-08 Mon  OC_HOLIDAY        00:00-07:00 rate=None fixed=112 prio=4 spans=False gen=True
"""


def test_golden_master_matches():
    """The full rule set for the pinned years is byte-for-byte what it was."""
    assert _dump_all() == GOLDEN.splitlines()


def test_inert_base_rules_are_exactly_the_known_three():
    """A rule with neither days nor specific_dates matches nothing and can never pay out.

    Three entries in data/oncall_rules.json are templates rather than live rules: they
    ship with empty specific_dates, and the generator emits its own rules with the rate
    hardcoded instead of reading these. Editing their fixed_hourly_rate in the JSON
    therefore changes nothing. Pinned here so the situation cannot quietly grow.
    """
    inert = [r.code for r in build_oncall_rules_for_year(2026) if not r.days and not r.specific_dates]
    assert inert == ["OC_HOLIDAY_EVE", "OC_HOLIDAY", "OC_SPECIAL"], inert


def test_label_is_a_function_of_code():
    """Labels are left out of the golden lines above, so pin the code to label map here."""
    labels = {}
    for year in GOLDEN_YEARS:
        for rule in build_oncall_rules_for_year(year):
            assert labels.setdefault(rule.code, rule.label) == rule.label, rule.code
    assert labels == {
        "OC_WEEKDAY": "Beredskap vardag",
        "OC_WEEKEND": "Beredskap helg",
        "OC_WEEKEND_SAT": "Beredskap helg l\u00f6rdag",
        "OC_WEEKEND_SUN": "Beredskap helg s\u00f6ndag",
        "OC_WEEKEND_MON": "Beredskap helg m\u00e5ndag",
        "OC_HOLIDAY_EVE": "Beredskap helgdagsafton",
        "OC_HOLIDAY": "Beredskap r\u00f6d dag",
        "OC_SPECIAL": "Beredskap storhelg",
        "OC_NATIONALDAGEN": "Beredskap Nationaldagen",
    }


if __name__ == "__main__":
    print("\n".join(_dump_all()))
