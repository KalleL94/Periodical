import datetime
from fastapi import HTTPException, status

def validate_person_id(person_id: int) -> int:
    """
    Säkerställ att person_id är mellan 1 och 10.

    Returnerar person_id om det är giltigt, annars kastas 404.
    """
    if not 1 <= person_id <= 10:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Person not found",
        )
    return person_id

def validate_date_params(
    year: int,
    month: int | None,
    day: int | None,
) -> datetime.date | None:
    """
    Validerar att ett givet datum är giltigt.

    - Om både month och day är satta: validera genom att skapa ett datum.
      Returnerar datetime.date om det lyckas.
    - Om enbart year + month: validera att year/month är giltigt genom att skapa dag 1.
      Returnerar None (anroparen bryr sig inte om själva datumet).
    - Om endast year: gör ingen validering och returnerar None.
    - Ogiltiga kombinationer eller värden ger HTTP 400.
    """
    try:
        if month is not None and day is not None:
            # Fullt datum, validera och returnera
            return datetime.date(year, month, day)

        if month is not None and day is None:
            # Validera månad (genom att testa dag 1)
            datetime.date(year, month, 1)
            return None

        if month is None and day is None:
            # Bara år, inget att validera här
            return None

        # month är None men day är satt -> orimlig kombination
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid date parameter combination",
        )
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid date",
        )



        
