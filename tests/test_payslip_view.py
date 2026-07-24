"""Route tests for the payslip page: rendering, overrides and upload comparison."""

import datetime
from pathlib import Path

import pytest

from app.auth.auth import create_access_token
from app.database.database import PersonHistory, User, UserRole, WageType

# The anonymised fixture rather than temp/Lönespec: the real payslips are
# gitignored, so a test that reads them has no data to run on in CI.
FIXTURE_PDF = Path(__file__).resolve().parent / "fixtures" / "payslip_202606.pdf"


def _login(client, user):
    client.cookies.set("access_token", f"Bearer {create_access_token(data={'sub': str(user.id)})}")


def _make_user(db, user_id, position, wage_type, wage):
    """A user holding a rotation position, the way the personal views resolve them."""
    user = User(
        id=user_id,
        username=f"user{user_id}",
        password_hash="x",
        name=f"User {user_id}",
        role=UserRole.USER,
        wage=wage,
        wage_type=wage_type,
        person_id=position,
        vacation={},
        must_change_password=0,
    )
    db.add(user)
    db.add(
        PersonHistory(
            user_id=user_id,
            person_id=position,
            name=user.name,
            username=user.username,
            is_active=1,
            effective_from=datetime.date(2026, 1, 1),
        )
    )
    db.commit()
    return user


@pytest.fixture
def monthly_user(test_db):
    return _make_user(test_db, 5, 5, WageType.MONTHLY, 37000)


@pytest.fixture
def hourly_user(test_db):
    return _make_user(test_db, 6, 6, WageType.HOURLY, 252)


def test_payslip_renders_for_monthly_user(test_client, monthly_user):
    _login(test_client, monthly_user)
    response = test_client.get("/month/5/payslip?year=2026&month=6")

    assert response.status_code == 200
    assert "Lönespecifikation" in response.text
    assert "Månadslön" in response.text
    assert "20260601-20260630" in response.text


def test_payslip_renders_for_hourly_user(test_client, hourly_user):
    _login(test_client, hourly_user)
    response = test_client.get("/month/6/payslip?year=2026&month=6")

    assert response.status_code == 200
    # An hourly user has no monthly base row; the worked hours are the base pay.
    assert "Arbetade timmar" in response.text


def test_get_on_the_compare_url_redirects_instead_of_405(test_client, hourly_user):
    """The comparison renders on a POST-only URL from an unstored upload.

    A language switch, refresh or back button lands on it as a GET, which must
    redirect to the payslip page rather than return 405 Method Not Allowed.
    """
    _login(test_client, hourly_user)
    response = test_client.get("/month/6/payslip/compare?year=2026&month=6", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/month/6/payslip?year=2026&month=6"


def test_payslip_is_forbidden_for_another_persons_month(test_client, monthly_user, hourly_user):
    _login(test_client, hourly_user)
    assert test_client.get("/month/5/payslip?year=2026&month=6").status_code == 403


def test_payslip_requires_login(test_client, monthly_user):
    response = test_client.get("/month/5/payslip?year=2026&month=6", follow_redirects=False)
    assert response.status_code == 302
    assert "/login" in response.headers["location"]


def test_override_changes_the_row_and_reaches_month_and_year(test_client, monthly_user, test_db):
    _login(test_client, monthly_user)

    from app.core.schedule.summary import summarize_month_for_person, summarize_year_for_person

    before = summarize_month_for_person(2026, 6, 5, session=test_db, wage_user_id=5, fetch_tax_table=False)
    computed_base = before["payslip"].by_key()["base"].amount
    year_before = summarize_year_for_person(2026, 5, session=test_db, wage_user_id=5)

    response = test_client.post(
        "/month/5/payslip/override",
        data={"year": 2026, "month": 6, "row_key": "base", "amount": "40000", "reason": "enligt lönespec"},
        follow_redirects=False,
    )
    assert response.status_code == 303

    page = test_client.get("/month/5/payslip?year=2026&month=6")
    assert "40 000" in page.text
    assert "Manuell" in page.text

    delta = 40000.0 - computed_base
    after = summarize_month_for_person(2026, 6, 5, session=test_db, wage_user_id=5, fetch_tax_table=False)
    assert round(after["brutto_pay"] - before["brutto_pay"], 2) == round(delta, 2)

    year_after = summarize_year_for_person(2026, 5, session=test_db, wage_user_id=5)
    assert round(year_after["year_summary"]["total_brutto"] - year_before["year_summary"]["total_brutto"], 2) == round(
        delta, 2
    )


def test_override_amount_is_computed_from_quantity_and_unit_price(test_client, monthly_user, test_db):
    """A row can be entered as hours at an a-price, not only as a lump sum."""
    _login(test_client, monthly_user)
    from app.database.database import PayslipOverride

    response = test_client.post(
        "/month/5/payslip/override",
        data={"year": 2026, "month": 6, "row_key": "ot", "hours": "8", "unit_price": "422.86"},
        follow_redirects=False,
    )
    assert response.status_code == 303

    row = test_db.query(PayslipOverride).filter(PayslipOverride.row_key == "ot").one()
    assert round(row.amount, 2) == 3382.88
    assert row.hours == 8.0


def test_override_qty_and_price_do_not_delete_when_amount_is_blank(test_client, monthly_user, test_db):
    """Blank amount must not clear the override when quantity and price are given."""
    _login(test_client, monthly_user)
    from app.database.database import PayslipOverride

    test_client.post(
        "/month/5/payslip/override",
        data={"year": 2026, "month": 6, "row_key": "ot", "hours": "8", "unit_price": "422.86", "amount": ""},
    )
    assert test_db.query(PayslipOverride).filter(PayslipOverride.row_key == "ot").count() == 1


def test_override_is_removed_by_an_empty_amount(test_client, monthly_user, test_db):
    _login(test_client, monthly_user)
    from app.database.database import PayslipOverride

    payload = {"year": 2026, "month": 6, "row_key": "base"}
    test_client.post("/month/5/payslip/override", data={**payload, "amount": "40000"})
    assert test_db.query(PayslipOverride).count() == 1

    test_client.post("/month/5/payslip/override", data={**payload, "amount": ""})
    assert test_db.query(PayslipOverride).count() == 0


def test_override_rejects_another_users_payslip(test_client, monthly_user, hourly_user):
    _login(test_client, hourly_user)
    response = test_client.post(
        "/month/5/payslip/override",
        data={"year": 2026, "month": 6, "row_key": "base", "amount": "40000"},
    )
    assert response.status_code == 403


def test_upload_compares_a_real_payslip_pdf(test_client, monthly_user):
    _login(test_client, monthly_user)
    pdf = FIXTURE_PDF.read_bytes()

    response = test_client.post(
        "/month/5/payslip/compare",
        data={"year": 2026, "month": 6},
        files={"file": ("202606.pdf", pdf, "application/pdf")},
    )

    assert response.status_code == 200
    assert "Jämförelse" in response.text
    assert "Lönespec" in response.text


def test_upload_of_a_broken_file_shows_an_error_not_a_500(test_client, monthly_user):
    _login(test_client, monthly_user)

    response = test_client.post(
        "/month/5/payslip/compare",
        data={"year": 2026, "month": 6},
        files={"file": ("notes.txt", b"this is not a pdf", "text/plain")},
    )

    assert response.status_code == 200
    assert "Filen är inte en PDF." in response.text


def test_upload_over_the_size_limit_is_rejected(test_client, monthly_user):
    _login(test_client, monthly_user)

    response = test_client.post(
        "/month/5/payslip/compare",
        data={"year": 2026, "month": 6},
        files={"file": ("big.pdf", b"%PDF" + b"0" * (2 * 1024 * 1024), "application/pdf")},
    )

    assert response.status_code == 200
    assert "större än 2 MB" in response.text


def test_month_before_employment_redirects_to_the_month_view(test_client, test_db):
    """A month outside the tenure has no payslip: the base row would be a wage never paid."""
    user = _make_user(test_db, 7, 7, WageType.MONTHLY, 37000)
    _login(test_client, user)

    response = test_client.get("/month/7/payslip?year=2025&month=6", follow_redirects=False)

    assert response.status_code == 302
    assert response.headers["location"] == "/month/7?year=2025&month=6"


def test_upload_without_a_csrf_token_is_rejected(raw_client, monthly_user):
    """The upload is multipart, which the CSRF middleware must still enforce."""
    _login(raw_client, monthly_user)

    response = raw_client.post(
        "/month/5/payslip/compare",
        data={"year": 2026, "month": 6},
        files={"file": ("x.pdf", b"%PDF-1.4", "application/pdf")},
    )

    assert response.status_code == 403
