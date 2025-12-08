# app/core/config.py

from typing import Final, Dict


# ==========================
# OB-divisorer (hur OB räknas)
# ==========================

#: Standarddivisor för "vanlig" OB när inget annat anges.
#: Timlön = månadslön / OB_RATE_DIVISOR_STANDARD.
#: Värdet 600 kommer från din befintliga logik och lönemodell.
OB_RATE_DIVISOR_STANDARD: Final[int] = 600

#: Divisor för OB4 ("Helgdag 300").
#: Värdet 300 motsvarar att OB4 betalar dubbelt så mycket som standard-OB.
OB_RATE_DIVISOR_OB4: Final[int] = 300

#: Divisor för OB5 ("Storhelg 150").
#: Värdet 150 motsvarar att OB5 betalar fyra gånger standard-OB.
OB_RATE_DIVISOR_OB5: Final[int] = 150

#: Hjälpstruktur om du vill slå upp defaultdivisor per OB-kod.
#: Används idag framför allt för specialregler som skapas i Python.
OB_RATE_DIVISOR_BY_CODE: Final[Dict[str, int]] = {
    "OB4": OB_RATE_DIVISOR_OB4,
    "OB5": OB_RATE_DIVISOR_OB5,
    # OB1–OB3 använder normalt rate från JSON-konfigurationen.
}


# ==========================
# Datum och tidformat
# ==========================

#: ISO-format för datumsträngar (till exempel settings.rotation_start_date).
#: Värdet "%Y-%m-%d" matchar befintliga JSON- och settings-filer.
DATE_FORMAT_ISO: Final[str] = "%Y-%m-%d"

#: Format för tider i shift-definitioner och OB-regler (till exempel "14:00").
#: Värdet "%H:%M" matchar nuvarande shift_types.json och ob_rules.
TIME_FORMAT_HM: Final[str] = "%H:%M"

#: Sträng som representerar "slutet av dagen" i OB-regler, till exempel "24:00".
#: Används för att särskilja fall där ett OB-intervall sträcker sig till midnatt
#: nästa dag.
TIME_END_OF_DAY_STRING: Final[str] = "24:00"