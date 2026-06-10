"""
Pytest configuration and shared fixtures for testing.

Provides reusable test fixtures:
- test_db: In-memory SQLite database for isolated testing
- test_client: FastAPI TestClient for API integration tests
- test_user: Mock authenticated user for protected routes
- admin_user: Mock admin user for admin route testing
"""

import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from starlette.routing import Mount

# Add project root to path for imports
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

# ruff: noqa: E402
import datetime

import app.database.database as db_module
from app.auth.auth import create_access_token, get_password_hash
from app.core.schedule import clear_schedule_cache
from app.database.database import Base, RotationEra, User, UserRole, WageType, get_db
from app.main import app

# A 10-week rotation pattern containing OFF, work (N1/N2/N3) and OC days, mirroring the
# characterization tests. Used by the rotation_session fixture so schedule lookups resolve.
_ROTATION_ERA_PATTERN = {
    "1": ["OFF", "OFF", "OFF", "N3", "N3", "N3", "N3"],
    "2": ["OFF", "OC", "N3", "N3", "N3", "N3", "OFF"],
    "3": ["OFF", "OFF", "N1", "N1", "N1", "N1", "OC"],
    "4": ["OC", "OFF", "N2", "N2", "N2", "OFF", "N1"],
    "5": ["N1", "N1", "N1", "N1", "OC", "OFF", "OFF"],
    "6": ["N3", "N3", "N3", "OFF", "OFF", "OC", "N3"],
    "7": ["N3", "N3", "OFF", "OC", "N2", "N2", "N2"],
    "8": ["N2", "N2", "OFF", "OFF", "N1", "N1", "N1"],
    "9": ["N1", "N1", "OC", "OFF", "OFF", "N2", "N2"],
    "10": ["N2", "N2", "N2", "N2", "OFF", "OFF", "OFF"],
}


@pytest.fixture(scope="function")
def test_db():
    """
    Create an in-memory SQLite database for testing.

    This fixture creates a fresh database for each test function,
    ensuring test isolation. The database is destroyed after each test.

    Yields:
        SQLAlchemy Session: Database session for test use
    """
    # Create in-memory database.
    # StaticPool keeps a single shared connection so the schema survives commits and is
    # visible across threads (the TestClient runs requests off the main thread); without
    # it, an in-memory SQLite DB silently loses its tables after a mid-request commit.
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    # Create all tables
    Base.metadata.create_all(bind=engine)

    # Create session
    db = TestingSessionLocal()

    try:
        yield db
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine)


@pytest.fixture(scope="function")
def test_client(test_db):
    """
    Create FastAPI TestClient with test database dependency override.

    This fixture provides a TestClient that uses the in-memory test
    database instead of the production database. All database queries
    during API tests will use the test database.

    Args:
        test_db: Test database session fixture

    Yields:
        TestClient: FastAPI test client for API testing
    """

    def override_get_db():
        try:
            yield test_db
        finally:
            pass

    # Mounted sub-apps (/api/v1, /api/v1/admin) have their own dependency_overrides,
    # so the override must be applied to each of them as well as the main app.
    sub_apps = [route.app for route in app.routes if isinstance(route, Mount) and isinstance(route.app, FastAPI)]
    for target in [app, *sub_apps]:
        target.dependency_overrides[get_db] = override_get_db

    with TestClient(app) as client:
        yield client

    # Clean up
    for target in [app, *sub_apps]:
        target.dependency_overrides.clear()


@pytest.fixture(scope="function")
def test_user(test_db):
    """
    Create a test user in the database for authentication testing.

    Creates user with:
    - username: testuser
    - password: testpass123
    - role: USER
    - wage: 35000

    Args:
        test_db: Test database session fixture

    Returns:
        User: Created test user object
    """
    user = User(
        id=1,
        username="testuser",
        password_hash=get_password_hash("testpass123"),
        name="Test User",
        role=UserRole.USER,
        wage=35000,
        vacation={},
        must_change_password=0,
    )
    test_db.add(user)
    test_db.commit()
    test_db.refresh(user)
    return user


@pytest.fixture(scope="function")
def admin_user(test_db):
    """
    Create a test admin user in the database.

    Creates admin user with:
    - username: admin
    - password: adminpass123
    - role: ADMIN
    - wage: 45000

    Args:
        test_db: Test database session fixture

    Returns:
        User: Created admin user object
    """
    admin = User(
        id=2,
        username="admin",
        password_hash=get_password_hash("adminpass123"),
        name="Admin User",
        role=UserRole.ADMIN,
        wage=45000,
        vacation={},
        must_change_password=0,
    )
    test_db.add(admin)
    test_db.commit()
    test_db.refresh(admin)
    return admin


@pytest.fixture(scope="function")
def auth_headers(test_user):
    """
    Generate JWT authentication headers for test requests.

    Creates a valid JWT token for the test user and returns
    headers dictionary ready to use with TestClient requests.

    Args:
        test_user: Test user fixture

    Returns:
        dict: Headers with Authorization bearer token
    """
    token = create_access_token(data={"sub": test_user.username})
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(scope="function")
def admin_headers(admin_user):
    """
    Generate JWT authentication headers for admin test requests.

    Creates a valid JWT token for the admin user and returns
    headers dictionary ready to use with TestClient requests.

    Args:
        admin_user: Admin user fixture

    Returns:
        dict: Headers with Authorization bearer token
    """
    token = create_access_token(data={"sub": admin_user.username})
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(scope="function")
def rotation_session(monkeypatch):
    """Session with a seeded rotation era and a position-1 user.

    Schedule lookups (rotation era, vacation/parental dates) use the global SessionLocal
    rather than an injected session, so we monkeypatch it onto a dedicated in-memory engine
    and seed the era there. This makes rotation-dependent assertions deterministic in CI,
    where no rotation era exists by default.
    """
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)
    monkeypatch.setattr(db_module, "SessionLocal", SessionLocal)
    clear_schedule_cache()

    session = SessionLocal()
    session.add(
        RotationEra(
            start_date=datetime.date(2026, 1, 2),
            end_date=None,
            rotation_length=10,
            weeks_pattern=_ROTATION_ERA_PATTERN,
        )
    )
    session.add(
        User(
            id=1,
            username="rotuser",
            password_hash="x",
            name="Rotation",
            role=UserRole.USER,
            wage=30000,
            wage_type=WageType.MONTHLY,
            person_id=1,
            vacation={},
            must_change_password=0,
        )
    )
    session.commit()

    yield session

    session.close()
    clear_schedule_cache()
    Base.metadata.drop_all(bind=engine)
