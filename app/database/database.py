# app/database/database.py
"""
SQLAlchemy database setup and models.
"""

import enum
from datetime import datetime

from sqlalchemy import (
    JSON,
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Time,
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


class OnCallOverrideType(str, enum.Enum):
    """Types of on-call override."""

    ADD = "ADD"  # Manuellt tillagt OC-pass
    REMOVE = "REMOVE"  # Avbokat OC-pass från rotation


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
    must_change_password = Column(
        Integer, default=1, nullable=False
    )  # 1=True, 0=False (SQLite uses integers for booleans)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    # Fixed syntax: foreign_keys as a direct string reference avoids evaluation errors
    overtime_shifts = relationship("OvertimeShift", foreign_keys="OvertimeShift.user_id", back_populates="user")


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
