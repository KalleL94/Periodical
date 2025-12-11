# app/database/database.py
"""
SQLAlchemy database setup and models.
"""

from datetime import datetime
from sqlalchemy import create_engine, Column, Integer, String, JSON, DateTime, Enum as SQLEnum, ForeignKey, Date, Time, Float
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship
import enum

DATABASE_URL = "sqlite:///./app/database/schedule.db"

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

class UserRole(str, enum.Enum):
    USER = "user"
    ADMIN = "admin"


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
