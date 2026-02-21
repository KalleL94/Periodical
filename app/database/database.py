# app/database/database.py
"""
SQLAlchemy database setup and models.
"""

import enum
from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    Time,
    UniqueConstraint,
    create_engine,
)
from sqlalchemy import Enum as SQLEnum
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship, sessionmaker

DATABASE_URL = "sqlite:///./app/database/schedule.db"

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


class UserRole(str, enum.Enum):
    USER = "user"
    ADMIN = "admin"


class AbsenceType(str, enum.Enum):
    """Types of absence with different compensation rules."""

    SICK = "SICK"  # Sjukfrånvaro - ger sjuklön efter dag 1
    VAB = "VAB"  # Vård av barn - ingen extra ersättning
    LEAVE = "LEAVE"  # Ledigt/Permission - ingen extra ersättning
    OFF = "OFF"  # Ledig - inget löneavdrag
    VACATION = "VACATION"  # Enskild semesterdag


class OnCallOverrideType(str, enum.Enum):
    """Types of on-call override."""

    ADD = "ADD"  # Manuellt tillagt OC-pass
    REMOVE = "REMOVE"  # Avbokat OC-pass från rotation


class ConsultantSalaryType(str, enum.Enum):
    """Whether the consultant's salary is paid for the current or previous month."""

    TRAILING = "trailing"  # Släpande — lön för föregående månad
    CURRENT = "current"  # Innestående — lön för aktuell månad


class SwapStatus(str, enum.Enum):
    """Status of a shift swap request."""

    PENDING = "PENDING"
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"


class User(Base):
    """User model with authentication and schedule data."""

    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(50), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)
    name = Column(String(100), nullable=False)
    role = Column(SQLEnum(UserRole), default=UserRole.USER, nullable=False)
    wage = Column(Integer, nullable=False)
    vacation = Column(JSON, default=dict)  # {"2026": [1,2,3], "2027": []}
    tax_table = Column(String(10), default="33", nullable=True)  # Swedish tax table number (e.g., "29", "30", "33")
    is_active = Column(
        Integer, default=1, nullable=False
    )  # 1=active, 0=inactive (allows filtering without PersonHistory queries)
    must_change_password = Column(
        Integer, default=1, nullable=False
    )  # 1=True, 0=False (SQLite uses integers for booleans)
    person_id = Column(
        Integer, nullable=True
    )  # Rotation position (1-10). If NULL, defaults to user.id for legacy compatibility
    employment_start_date = Column(Date, nullable=True)  # When employee started working (for vacation balance)
    vacation_year_start_month = Column(
        Integer, default=4, nullable=False
    )  # Brytmånad: month (1-12) when vacation year starts (default April)
    vacation_days_per_year = Column(
        Integer, default=25, nullable=False
    )  # Annual vacation entitlement (Swedish standard: 25)
    vacation_saved = Column(
        JSON, default=dict
    )  # Saved vacation days per year: {"2025": {"saved": 3, "paid_out": 2, "payout_amount": 3404.0}}
    custom_rates = Column(JSON, default=dict)  # Per-user rate overrides (OB, OT, oncall, vacation)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    @property
    def rotation_person_id(self) -> int:
        """Get the rotation position for this user. Falls back to user.id if not set."""
        return self.person_id if self.person_id is not None else self.id

    # Relationships
    # Fixed syntax: foreign_keys as a direct string reference avoids evaluation errors
    overtime_shifts = relationship("OvertimeShift", foreign_keys="OvertimeShift.user_id", back_populates="user")
    employment_transition = relationship(
        "EmploymentTransition", back_populates="user", uselist=False, cascade="all, delete-orphan"
    )


class OvertimeShift(Base):
    """Overtime shift model for tracking called-in shifts during on-call."""

    __tablename__ = "overtime_shifts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    date = Column(Date, nullable=False)
    start_time = Column(Time, nullable=False)
    end_time = Column(Time, nullable=False)
    hours = Column(Float, nullable=False)
    ot_pay = Column(Float, nullable=False)
    is_extension = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    created_by = Column(Integer, ForeignKey("users.id"))

    # Relationships
    user = relationship("User", foreign_keys=[user_id], back_populates="overtime_shifts")
    creator = relationship("User", foreign_keys=[created_by])

    def __repr__(self):
        return f"<OvertimeShift(id={self.id}, user_id={self.user_id}, date={self.date}, hours={self.hours})>"


class Absence(Base):
    """Absence model for tracking different types of absence (sick leave, VAB, etc)."""

    __tablename__ = "absences"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    date = Column(Date, nullable=False)
    absence_type = Column(SQLEnum(AbsenceType), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    user = relationship("User", foreign_keys=[user_id])

    def __repr__(self):
        return f"<Absence(id={self.id}, user_id={self.user_id}, date={self.date}, type={self.absence_type})>"


class OnCallOverride(Base):
    """On-call override model for manually adding or removing on-call shifts."""

    __tablename__ = "oncall_overrides"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    date = Column(Date, nullable=False)
    override_type = Column(SQLEnum(OnCallOverrideType), nullable=False)
    reason = Column(String(255), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)

    # Relationships
    user = relationship("User", foreign_keys=[user_id])
    creator = relationship("User", foreign_keys=[created_by])

    def __repr__(self):
        return f"<OnCallOverride(id={self.id}, user_id={self.user_id}, date={self.date}, type={self.override_type})>"


class WageHistory(Base):
    """Wage history model for tracking wage changes over time with temporal validity."""

    __tablename__ = "wage_history"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    wage = Column(Integer, nullable=False)  # Monthly wage in SEK
    effective_from = Column(Date, nullable=False)  # When this wage becomes effective
    effective_to = Column(Date, nullable=True)  # When this wage ends (NULL = current wage)
    created_at = Column(DateTime, default=datetime.utcnow)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)

    # Relationships
    user = relationship("User", foreign_keys=[user_id])
    creator = relationship("User", foreign_keys=[created_by])

    def __repr__(self):
        return (
            f"<WageHistory(id={self.id}, user_id={self.user_id}, wage={self.wage}, "
            f"effective_from={self.effective_from}, effective_to={self.effective_to})>"
        )


class RateHistory(Base):
    """Rate history model for tracking per-user rate changes over time.

    Mirrors WageHistory pattern: effective_from/effective_to with NULL = current.
    The rates JSON stores only overrides (same format as User.custom_rates).
    """

    __tablename__ = "rate_history"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    rates = Column(JSON, nullable=False)  # Same format as User.custom_rates
    effective_from = Column(Date, nullable=False)
    effective_to = Column(Date, nullable=True)  # NULL = current
    created_at = Column(DateTime, default=datetime.utcnow)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)

    user = relationship("User", foreign_keys=[user_id])
    creator = relationship("User", foreign_keys=[created_by])

    def __repr__(self):
        return (
            f"<RateHistory(id={self.id}, user_id={self.user_id}, "
            f"effective_from={self.effective_from}, effective_to={self.effective_to})>"
        )


class PersonHistory(Base):
    """Person history model for tracking person changes over time with temporal validity.

    Tracks who occupied each person_id (position 1-10) during which time periods.
    This enables:
    - Old employees to see their own historical data after leaving
    - New employees to only see data from their start date
    - Admin to see all data with correct person names per time period
    """

    __tablename__ = "person_history"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)  # Which User occupied this position
    person_id = Column(Integer, nullable=False)  # Which position (1-10) in rotation
    name = Column(String(100), nullable=False)  # Person's name during this period
    username = Column(String(50), nullable=False)  # Username during this period
    is_active = Column(Integer, nullable=False)  # 1=active, 0=inactive during this period
    effective_from = Column(Date, nullable=False)  # Start date of this employment period
    effective_to = Column(Date, nullable=True)  # End date (NULL = currently employed)
    created_at = Column(DateTime, default=datetime.utcnow)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)

    # Relationships
    user = relationship("User", foreign_keys=[user_id])
    creator = relationship("User", foreign_keys=[created_by])

    def __repr__(self):
        return (
            f"<PersonHistory(id={self.id}, user_id={self.user_id}, person_id={self.person_id}, "
            f"name={self.name}, effective_from={self.effective_from}, effective_to={self.effective_to})>"
        )


class RotationEra(Base):
    """Rotation era model for tracking rotation configuration changes over time.

    Allows the system to change rotation length (e.g., from 10 to 11 weeks) without
    corrupting historical schedule calculations. Each era defines a time period with
    its own rotation parameters.
    """

    __tablename__ = "rotation_eras"

    id = Column(Integer, primary_key=True, autoincrement=True)
    start_date = Column(Date, nullable=False, index=True)  # When this era begins
    end_date = Column(Date, nullable=True, index=True)  # When this era ends (NULL = current/ongoing)
    rotation_length = Column(Integer, nullable=False)  # Number of weeks in rotation cycle
    weeks_pattern = Column(JSON, nullable=False)  # Week definitions: {"1": ["OFF", "OFF", ...], ...}
    created_at = Column(DateTime, default=datetime.utcnow)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)

    # Relationships
    creator = relationship("User", foreign_keys=[created_by])

    def __repr__(self):
        return (
            f"<RotationEra(id={self.id}, start_date={self.start_date}, "
            f"end_date={self.end_date}, rotation_length={self.rotation_length})>"
        )


class ShiftSwap(Base):
    """Shift swap request between two users, potentially on different dates."""

    __tablename__ = "shift_swaps"

    id = Column(Integer, primary_key=True, autoincrement=True)
    requester_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    target_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    requester_date = Column(Date, nullable=False)  # Date requester gives away
    target_date = Column(Date, nullable=False)  # Date requester wants from target
    requester_shift_code = Column(String(10), nullable=True)
    target_shift_code = Column(String(10), nullable=True)
    status = Column(SQLEnum(SwapStatus), default=SwapStatus.PENDING, nullable=False)
    message = Column(String(255), nullable=True)
    responded_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    requester = relationship("User", foreign_keys=[requester_id])
    target = relationship("User", foreign_keys=[target_id])

    def __repr__(self):
        return (
            f"<ShiftSwap(id={self.id}, requester={self.requester_id}, "
            f"target={self.target_id}, date={self.date}, status={self.status})>"
        )


class EmploymentTransition(Base):
    """Configuration for a user's transition from consultant to direct employment.

    Stores all parameters needed to calculate the one-time vacation payout (semesterlagen)
    and the split-employer salary in the transition month. One record per user (unique).
    """

    __tablename__ = "employment_transitions"
    __table_args__ = (UniqueConstraint("user_id", name="uq_employment_transitions_user_id"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    transition_date = Column(Date, nullable=False)  # First day as direct employee (Handels)
    consultant_salary_type = Column(SQLEnum(ConsultantSalaryType), nullable=False)  # TRAILING or CURRENT
    consultant_vacation_days = Column(Float, nullable=False, default=0.0)  # Days to pay out
    consultant_supplement_pct = Column(Float, nullable=False, default=0.0043)  # Semesterlagen minimum: 0.43% per dag
    variable_avg_daily_override = Column(Float, nullable=True)  # Manual override; NULL = auto-calculate from history
    earning_year_start = Column(Date, nullable=True)  # NULL = auto: April 1 two years back
    earning_year_end = Column(Date, nullable=True)  # NULL = auto: day before transition_date
    advance_vacation_days = Column(Integer, nullable=True, default=None)  # Forskottsemester fran Handels
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    user = relationship("User", back_populates="employment_transition")

    def __repr__(self):
        return (
            f"<EmploymentTransition(id={self.id}, user_id={self.user_id}, "
            f"transition_date={self.transition_date}, type={self.consultant_salary_type})>"
        )


def create_tables():
    """Create all database tables."""
    Base.metadata.create_all(bind=engine)


def get_db():
    """Dependency for getting database session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
