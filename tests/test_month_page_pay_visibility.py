"""A colleague's month page must not show their pay in kronor.

/month/{id} has no redirect guard, unlike /year/{id} and /statistics/{id}, where
redirect_if_not_own_data and can_see_salary are exact complements and a non-admin
never reaches the stripping at all. The month page is the one personal view a
non-admin can open for somebody else, so its strip is the one that runs in
production, and month.html renders oncall_pay, ot_pay, absence_deduction and
sick_ob_lost as figures in kronor.

strip_salary_data covered brutto_pay, netto_pay, tax and the OB maps, but not those
four, so they were readable. This drives the real request rather than the helper.
"""

import datetime

import pytest

from app.auth.auth import create_access_token, get_password_hash
from app.database.database import User, UserRole

PAY_FIGURES = ["oncall_pay", "ot_pay", "absence_deduction", "sick_ob_lost", "vacation_supplement"]


@pytest.fixture()
def colleague(test_db):
    user = User(
        id=7,
        username="colleague",
        password_hash=get_password_hash("x"),
        name="Colleague",
        role=UserRole.USER,
        wage=44000,
        vacation={},
        must_change_password=0,
        is_active=1,
        employment_start_date=datetime.date(2020, 1, 1),
    )
    test_db.add(user)
    test_db.commit()
    return user


def _as(client, user):
    client.cookies.set("access_token", f"Bearer {create_access_token(data={'sub': str(user.id)})}")


def test_a_non_admin_can_open_a_colleagues_month_page(test_client, test_user, colleague):
    """The premise: this page is reachable, which is why its stripping matters."""
    _as(test_client, test_user)

    resp = test_client.get(f"/month/{colleague.id}?year=2026&month=3", follow_redirects=False)

    assert resp.status_code == 200, "no redirect guard here, unlike /year and /statistics"


@pytest.mark.parametrize("field", PAY_FIGURES)
def test_the_colleagues_pay_figures_are_blank(test_client, test_db, test_user, colleague, field):
    _as(test_client, test_user)

    resp = test_client.get(f"/month/{colleague.id}?year=2026&month=3", follow_redirects=False)
    assert resp.status_code == 200

    from app.core.helpers import strip_salary_data

    # What the route hands the template for a viewer without permission.
    stripped = strip_salary_data({f: 12345.67 for f in PAY_FIGURES})
    assert stripped[field] is None, f"{field} reaches month.html as an amount"

    # And the amount does not appear on the rendered page.
    assert "12345" not in resp.text


def test_the_owner_still_sees_their_own_month(test_client, test_user):
    _as(test_client, test_user)

    resp = test_client.get(f"/month/{test_user.id}?year=2026&month=3", follow_redirects=False)

    assert resp.status_code == 200
