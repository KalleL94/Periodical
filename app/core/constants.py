# app/core/constants.py
from typing import Final, Tuple, Dict


# ==========================
# Personer / användare
# ==========================

#: Maximalt antal schemalagda personer i systemet.
#: Värdet 10 kommer från din nuvarande data (personer 1 till 10).
MAX_PERSONS: Final[int] = 10

#: Tuple med alla giltiga person-id:n.
#: Används i stället för hårdkodad range(1, 11).
PERSON_IDS: Final[Tuple[int, ...]] = tuple(range(1, MAX_PERSONS + 1))


# ==========================
# Skiftkoder
# ==========================

#: Kod för dagpass (N1) enligt Ica-rotationen.
SHIFT_CODE_N1: Final[str] = "N1"

#: Kod för kvällspass (N2) enligt Ica-rotationen.
SHIFT_CODE_N2: Final[str] = "N2"

#: Kod för nattpass (N3) enligt Ica-rotationen.
SHIFT_CODE_N3: Final[str] = "N3"

#: Kod för ledig dag (ingen arbetstid).
SHIFT_CODE_OFF: Final[str] = "OFF"

#: Kod för semester (används både som OB-logik och som "syntetiskt" skift).
SHIFT_CODE_SEMESTER: Final[str] = "SEM"

#: Samlingslista över skiftkoder som används i logik (till exempel jämförelser).
SHIFT_CODES: Final[Tuple[str, ...]] = (
    SHIFT_CODE_N1,
    SHIFT_CODE_N2,
    SHIFT_CODE_N3,
    SHIFT_CODE_OFF,
    SHIFT_CODE_SEMESTER,
)

#: Skiftkoder som räknas som "riktiga arbetspass" i cowork-logiken.
WORK_SHIFT_CODES: Final[Tuple[str, ...]] = (
    SHIFT_CODE_N1,
    SHIFT_CODE_N2,
    SHIFT_CODE_N3,
)


# ==========================
# OB-koder och prioritet
# ==========================

#: Standardlista över OB-koder enligt avtalet.
OB_CODES: Final[Tuple[str, ...]] = ("OB1", "OB2", "OB3", "OB4", "OB5")

#: OB-koder som används i årsöversikter och sammanställningar.
OB_CODES_FOR_SUMMARY: Final[Tuple[str, ...]] = OB_CODES

#: Prioritetsordning när OB-regler överlappar varandra.
#: Högre värde vinner. Här är OB5 högst, sedan OB4, övriga lika.
OB_PRIORITY_BY_CODE: Final[Dict[str, int]] = {
    "OB5": 3,
    "OB4": 2,
    # OB1–OB3 får defaultprioritet (se OB_PRIORITY_DEFAULT).
}

#: Standardprioritet för OB-koder som inte är listade explicit i OB_PRIORITY_BY_CODE.
OB_PRIORITY_DEFAULT: Final[int] = 1


# ==========================
# Veckostruktur / datum
# ==========================

#: Antal dagar per vecka. Används i loops i stället för "7".
DAYS_PER_WEEK: Final[int] = 7

#: Antal timmar per dygn. Används i tidssummeringar.
HOURS_PER_DAY: Final[int] = 24

#: Antal sekunder per timme. Används vid konvertering från delta till timmar.
SECONDS_PER_HOUR: Final[int] = 3600

#: Index för veckans första dag i Python datetime (0 = måndag).
#: All logik i systemet bygger på att rotationen börjar en måndag.
WEEK_START_WEEKDAY: Final[int] = 0  # Monday


# ==========================
# Veckodagsnamn (presentation)
# ==========================

#: Svenska namn på veckodagar, indexerade som datetime.weekday() (0=måndag, 6=söndag).
#: Används för visning i templates.
WEEKDAY_NAMES: Final[Tuple[str, ...]] = (
    "Måndag",
    "Tisdag",
    "Onsdag",
    "Torsdag",
    "Fredag",
    "Lördag",
    "Söndag",
)


# ==========================
# Semester / frånvaro
# ==========================

#: Standardkod för semester i persons.json och i schemalogiken.
VACATION_CODE: Final[str] = SHIFT_CODE_SEMESTER