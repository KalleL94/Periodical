"""Bygger OB-regler för helgdagar."""

import datetime

from app.core.holidays import (
    alla_helgons_dag,
    annandagpask,
    first_weekday_after,
    forsta_maj,
    julafton,
    kristi_himmelsfardsdag,
    langfredagen,
    midsommarafton,
    nationaldagen,
    nyarsafton,
    nyarsdagen,
    pingstafton,
    skartorsdagen,
    trettondagen,
)
from app.core.models import ObRule


def build_special_ob_rules_for_year(year: int) -> list[ObRule]:
    """
    Bygger OB4/OB5-regler för ett år baserat på helgdagar.

    - OB4 (300): Trettondagen, 1 maj, nationaldagen, Kristi himmelsfärd, alla helgons dag
    - OB5 (150): Långfredag, påsk, pingst, midsommar, jul, nyår
    """
    rules: list[ObRule] = []

    # === OB4 (Helgdag - löneart 152) ===
    ob4_holidays = [
        (trettondagen(year), "07:00"),
        (forsta_maj(year), "07:00"),
        (nationaldagen(year), "07:00"),
        (kristi_himmelsfardsdag(year), "07:00"),
        (alla_helgons_dag(year), "07:00"),
    ]

    for holiday_date, start_time in ob4_holidays:
        rules.extend(_build_holiday_interval("OB4", "Helgdag (152)", holiday_date, start_time, 300))

    # === OB5 (Storhelg - löneart 153) ===

    # Skärtorsdag från 18:00
    rules.extend(_build_holiday_interval("OB5", "Storhelg (153)", skartorsdagen(year), "18:00", 150))

    # Långfredag från 00:00
    rules.extend(_build_holiday_interval("OB5", "Storhelg (153)", langfredagen(year), "00:00", 150))

    # Annandag påsk
    rules.extend(_build_holiday_interval("OB5", "Storhelg (153)", annandagpask(year), "00:00", 150))

    # Nyårshelgen (från föregående år)
    rules.extend(_build_new_years_rules(year))

    # Pingsthelgen
    pingst_eve = pingstafton(year)
    rules.extend(_build_eve_block("OB5", "Storhelg (153)", pingst_eve, pingst_eve + datetime.timedelta(days=1), 150))

    # Midsommarhelgen
    midsummer_eve = midsommarafton(year)
    rules.extend(
        _build_eve_block("OB5", "Storhelg (153)", midsummer_eve, midsummer_eve + datetime.timedelta(days=1), 150)
    )

    # Julhelgen
    christmas_eve = julafton(year)
    rules.extend(
        _build_eve_block("OB5", "Storhelg (153)", christmas_eve, christmas_eve + datetime.timedelta(days=2), 150)
    )

    return rules


def _build_holiday_interval(
    code: str,
    label: str,
    start_date: datetime.date,
    start_time: str,
    rate: int,
) -> list[ObRule]:
    """
    Bygger OB-regler från start_date kl start_time till första vardagen.
    """
    rules = []
    end_date = first_weekday_after(start_date)
    day = start_date
    first = True

    while day < end_date:
        st = start_time if first else "00:00"
        rules.append(
            ObRule(
                code=code,
                label=label,
                specific_dates=[day.isoformat()],
                start_time=st,
                end_time="24:00",
                rate=rate,
            )
        )
        first = False
        day += datetime.timedelta(days=1)

    return rules


def _build_eve_block(
    code: str,
    label: str,
    eve_date: datetime.date,
    last_holiday_date: datetime.date,
    rate: int,
) -> list[ObRule]:
    """
    Bygger OB-regler från 07:00 på aftonen till första vardagen.
    """
    rules = []
    end_date = first_weekday_after(last_holiday_date)
    day = eve_date
    first = True

    while day < end_date:
        st = "07:00" if first else "00:00"
        rules.append(
            ObRule(
                code=code,
                label=label,
                specific_dates=[day.isoformat()],
                start_time=st,
                end_time="24:00",
                rate=rate,
            )
        )
        first = False
        day += datetime.timedelta(days=1)

    return rules


def _build_new_years_rules(year: int) -> list[ObRule]:
    """Bygger nyårsregler (från föregående år och för aktuellt år)."""
    rules = []

    # Nyårsafton föregående år -> nyårsdagen detta år
    ny_prev = nyarsafton(year - 1)
    end_prev = first_weekday_after(nyarsdagen(year))
    day = ny_prev
    first = True

    while day < end_prev:
        st = "18:00" if first else "00:00"
        rules.append(
            ObRule(
                code="OB5",
                label="Storhelg (153)",
                specific_dates=[day.isoformat()],
                start_time=st,
                end_time="24:00",
                rate=150,
            )
        )
        first = False
        day += datetime.timedelta(days=1)

    # Nyårsafton detta år -> nyårsdagen nästa år
    ny_this = nyarsafton(year)
    end_this = first_weekday_after(nyarsdagen(year + 1))
    day = ny_this
    first = True

    while day < end_this:
        st = "18:00" if first else "00:00"
        rules.append(
            ObRule(
                code="OB5",
                label="Storhelg (153)",
                specific_dates=[day.isoformat()],
                start_time=st,
                end_time="24:00",
                rate=150,
            )
        )
        first = False
        day += datetime.timedelta(days=1)

    return rules
