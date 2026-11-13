"""The /api/ access log must identify the caller and the exact request.

An integration that shows the wrong thing is debugged from the log afterwards,
so a line has to answer three questions on its own: who called, what did they
ask for (query string included), and what did they get.
"""

import logging

import pytest
from sqlalchemy.orm import sessionmaker

import app.database.database as db_module
from app.auth.auth import hash_api_key
from app.database.database import User, UserRole, WageType

API_KEY = "log-test-key"


@pytest.fixture()
def logged_client(test_db, test_client, monkeypatch):
    engine = test_db.get_bind()
    monkeypatch.setattr(db_module, "SessionLocal", sessionmaker(autocommit=False, autoflush=False, bind=engine))
    test_db.add(
        User(
            id=1,
            username="logger",
            password_hash="x",
            name="Log User",
            role=UserRole.USER,
            wage=30000,
            wage_type=WageType.MONTHLY,
            vacation={},
            must_change_password=0,
            is_active=1,
            person_id=1,
            api_key=hash_api_key(API_KEY),
        )
    )
    test_db.commit()
    return test_client


def _api_records(caplog):
    return [r for r in caplog.records if r.name == "app.api"]


def test_api_request_is_logged_with_caller_and_query(logged_client, caplog):
    with caplog.at_level(logging.INFO):
        resp = logged_client.get(
            "/api/v1/users/1/status?date=2026-03-15&time=03:00",
            headers={"Authorization": f"Bearer {API_KEY}"},
        )
    assert resp.status_code == 200

    records = _api_records(caplog)
    assert len(records) == 1
    fields = records[0].extra_fields
    assert fields["path"] == "/api/v1/users/1/status"
    assert fields["query"] == "date=2026-03-15&time=03:00"
    assert fields["status_code"] == 200
    assert fields["username"] == "logger"
    assert fields["user_id"] == 1


def test_rejected_api_key_is_logged_as_a_warning(logged_client, caplog):
    with caplog.at_level(logging.INFO):
        resp = logged_client.get("/api/v1/users/1/status", headers={"Authorization": "Bearer nope"})
    assert resp.status_code == 401

    records = _api_records(caplog)
    assert len(records) == 1
    assert records[0].levelno == logging.WARNING
    assert records[0].extra_fields["status_code"] == 401
    assert "username" not in records[0].extra_fields


def test_non_api_request_stays_out_of_the_api_log(logged_client, caplog):
    with caplog.at_level(logging.INFO):
        logged_client.get("/login")
    assert _api_records(caplog) == []


def test_url_secrets_are_redacted_from_the_request_log(logged_client, caplog):
    with caplog.at_level(logging.INFO):
        logged_client.get("/calendar/feed/feedsecret123/schema.ics")
        logged_client.get("/admin/report?year=2026&token=reportsecret456&month=3")
        logged_client.get("/admin/report?%74oken=encodedsecret789")

    # The test client's own httpx logger prints the raw URL; only the app's records count.
    app_records = [r for r in caplog.records if r.name.startswith("app")]
    assert "feedsecret123" not in "".join(r.getMessage() for r in app_records)
    logged_fields = str([getattr(r, "extra_fields", {}) for r in app_records])
    assert "reportsecret456" not in logged_fields
    assert "encodedsecret789" not in logged_fields
    fields = [r.extra_fields for r in caplog.records if hasattr(r, "extra_fields")]
    assert any(f.get("query") == "year=2026&token=[redacted]&month=3" for f in fields)
    assert any(f["path"] == "/calendar/feed/[redacted]/schema.ics" for f in fields)
