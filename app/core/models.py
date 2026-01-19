from datetime import date, datetime

from pydantic import BaseModel


class ShiftType(BaseModel):
    """Shift type definition with timing and display information."""

    code: str
    label: str | None = None
    start_time: str | None = None
    end_time: str | None = None
    color: str | None = None


class Rotation(BaseModel):
    """Work rotation schedule configuration."""

    rotation_length: int
    weeks: dict[str, list[str]]


class Settings(BaseModel):
    """Application settings and configuration."""

    rotation_start_date: str
    monthly_salary: int | None = None


class ObRule(BaseModel):
    """Unsocial hours (OB) rule definition."""

    code: str
    label: str
    days: list[int] | None = None
    specific_dates: list[str] | None = None
    start_time: str
    end_time: str
    rate: int


class OnCallRule(BaseModel):
    """On-call compensation rule definition."""

    code: str
    label: str
    days: list[int] | None = None
    specific_dates: list[str] | None = None
    start_time: str
    end_time: str
    rate: int | None = None  # Divisor: monthly_salary / rate = hourly compensation (deprecated)
    fixed_hourly_rate: int | None = None  # Fixed SEK per hour (preferred)
    priority: int = 1  # Higher priority replaces lower priority
    spans_to_next_day: bool = False
    generated: bool = False


class TaxBracket(BaseModel):
    """Tax bracket definition for income ranges."""

    lon_fran: float
    lon_till: float | None = None
    prel_skatt: float


class Person(BaseModel):
    """Person/employee definition with salary and vacation information."""

    id: int
    name: str
    wage: int
    vacation: dict[str, list[int]] | None = None  # key is year, value is list of weeks


class OvertimeShiftCreate(BaseModel):
    """Data for creating an overtime shift."""

    user_id: int
    date: date
    start_time: str  # "HH:MM" format
    end_time: str  # "HH:MM" format
    hours: float = 8.5  # Default 8.5 hours


class OvertimeShiftResponse(BaseModel):
    """Overtime shift response model."""

    id: int
    user_id: int
    date: date
    start_time: str
    end_time: str
    hours: float
    ot_pay: float
    created_at: datetime

    class Config:
        from_attributes = True
