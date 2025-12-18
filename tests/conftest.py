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
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Add project root to path for imports
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

# ruff: noqa: E402
from app.auth.auth import create_access_token, get_password_hash
from app.database.database import Base, User, UserRole, get_db
from app.main import app


@pytest.fixture(scope="function")
def test_db():
    """
    Create an in-memory SQLite database for testing.

    This fixture creates a fresh database for each test function,
    ensuring test isolation. The database is destroyed after each test.

    Yields:
        SQLAlchemy Session: Database session for test use
    """
    # Create in-memory database
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
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

    app.dependency_overrides[get_db] = override_get_db

    with TestClient(app) as client:
        yield client

    # Clean up
    app.dependency_overrides.clear()


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
