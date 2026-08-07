# app/core/constants.py
from typing import Final

# ==========================
# Personer / användare
# ==========================

#: Maximalt antal schemalagda personer i systemet.
#: Värdet 10 kommer från din nuvarande data (personer 1 till 10).
MAX_PERSONS: Final[int] = 10

#: Tuple med alla giltiga person-id:n.
#: Används i stället för hårdkodad range(1, 11).
PERSON_IDS: Final[tuple[int, ...]] = tuple(range(1, MAX_PERSONS + 1))


def placeholder_person_name(person_id: int) -> str:
    """Display name for a rotation position with nobody resolvable behind it.

    Reached when there is no session to ask and no PersonHistory or User row for
    the position. This used to read data/persons.json, a second roster kept
    beside the users table; after that file was anonymised it held exactly this
    string, so the file was a lookup that could only ever return its own index.
    """
    return f"Person {person_id}"


# ==========================
# Skiftkoder
# ==========================

#: Skiftkoder som räknas som "riktiga arbetspass" i cowork-logiken.
#: OC (Beredskap) exkluderas medvetet - Beredskap räknas inte i samarbetsstatistik.
WORK_SHIFT_CODES: Final[tuple[str, ...]] = ("N1", "N2", "N3")


# ==========================
# OB-koder och prioritet
# ==========================

#: Prioritetsordning när OB-regler överlappar varandra.
#: Högre värde vinner. Här är OB5 högst, sedan OB4, övriga lika.
OB_PRIORITY_BY_CODE: Final[dict[str, int]] = {
    "OB5": 3,
    "OB4": 2,
    # OB1–OB3 får defaultprioritet (se OB_PRIORITY_DEFAULT).
}

#: Standardprioritet för OB-koder som inte är listade explicit i OB_PRIORITY_BY_CODE.
OB_PRIORITY_DEFAULT: Final[int] = 1


# ==========================
# Veckodagsnamn (presentation)
# ==========================

#: Svenska namn på veckodagar, indexerade som datetime.weekday() (0=måndag, 6=söndag).
#: Används för visning i templates.
WEEKDAY_NAMES: Final[tuple[str, ...]] = (
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

#: Standardkod för semester i schemalogiken.
VACATION_CODE: Final[str] = "SEM"

# ==========================
# Löneberäkningar
# ==========================

#: Antal timmar per månad för timlönsberäkning vid övertid.
#: Formel: månadslön / OT_RATE_DIVISOR = timlön för OT-beräkning
OT_RATE_DIVISOR: Final[int] = 72

# ==========================
# Säkerhet / Autentisering
# ==========================

#: Standardlösenord vid lösenordsåterställning.
#: VARNING: Ändra detta i produktionsmiljö!
DEFAULT_PASSWORD: Final[str] = "London1"
