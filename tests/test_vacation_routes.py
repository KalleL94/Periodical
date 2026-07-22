"""Route tests for the self-service and admin vacation modules.

These two route sets are two copies of the same logic (profile.py operates on
the logged-in user, admin.py on a user_id from the path). Nothing exercised
them before, so the assertions here pin the current behaviour of both copies
and act as the safety net for sharing the implementation.
"""

import datetime

import pytest

from app.database.database import Absence, AbsenceType


def _login(client, username, password):
    client.post("/login", data={"username": username, "password": password})


@pytest.fixture
def user_client(test_client, test_user):
    _login(test_client, "testuser", "testpass123")
    return test_client


@pytest.fixture
def admin_client(test_client, admin_user, test_user):
    _login(test_client, "admin", "adminpass123")
    return test_client


def _absences(db, user_id, absence_type):
    return sorted(
        a.date for a in db.query(Absence).filter(Absence.user_id == user_id, Absence.absence_type == absence_type).all()
    )


class TestVacationWeeks:
    def test_profile_stores_sorted_deduped_valid_weeks(self, user_client, test_db, test_user):
        resp = user_client.post(
            "/profile/vacation",
            data={"year": 2026, "weeks": "30, 28,28, 0, 54, abc, 29"},
            follow_redirects=False,
        )

        assert resp.status_code == 302
        test_db.refresh(test_user)
        assert test_user.vacation["2026"] == [28, 29, 30]

    def test_profile_empty_weeks_clears_the_year(self, user_client, test_db, test_user):
        test_user.vacation = {"2026": [10]}
        test_db.commit()

        user_client.post("/profile/vacation", data={"year": 2026, "weeks": ""}, follow_redirects=False)

        test_db.refresh(test_user)
        assert test_user.vacation["2026"] == []

    def test_profile_out_of_range_year_is_a_noop(self, user_client, test_db, test_user):
        resp = user_client.post(
            "/profile/vacation",
            data={"year": 1999, "weeks": "10"},
            follow_redirects=False,
        )

        assert resp.status_code == 302
        test_db.refresh(test_user)
        assert "1999" not in (test_user.vacation or {})

    def test_admin_stores_sorted_deduped_valid_weeks(self, admin_client, test_db, test_user):
        resp = admin_client.post(
            f"/admin/vacation/{test_user.id}/weeks",
            data={"year": 2026, "weeks": "30, 28,28, 0, 54, abc, 29"},
            follow_redirects=False,
        )

        assert resp.status_code == 303
        test_db.refresh(test_user)
        assert test_user.vacation["2026"] == [28, 29, 30]

    def test_admin_out_of_range_year_is_a_noop(self, admin_client, test_db, test_user):
        resp = admin_client.post(
            f"/admin/vacation/{test_user.id}/weeks",
            data={"year": 1999, "weeks": "10"},
            follow_redirects=False,
        )

        assert resp.status_code == 302
        test_db.refresh(test_user)
        assert "1999" not in (test_user.vacation or {})

    def test_admin_unknown_user_redirects_to_the_list(self, admin_client):
        resp = admin_client.post(
            "/admin/vacation/9999/weeks",
            data={"year": 2026, "weeks": "10"},
            follow_redirects=False,
        )

        assert resp.status_code == 302
        assert resp.headers["location"] == "/admin/vacation"


class TestParentalWeeks:
    def test_profile_stores_parental_weeks(self, user_client, test_db, test_user):
        resp = user_client.post(
            "/profile/vacation/parental/weeks",
            data={"year": 2026, "weeks": "5,5,3,99"},
            follow_redirects=False,
        )

        assert resp.status_code == 302
        test_db.refresh(test_user)
        assert test_user.parental_leave["2026"] == [3, 5]

    def test_admin_stores_parental_weeks(self, admin_client, test_db, test_user):
        resp = admin_client.post(
            f"/admin/vacation/{test_user.id}/parental/weeks",
            data={"year": 2026, "weeks": "5,5,3,99"},
            follow_redirects=False,
        )

        assert resp.status_code == 303
        test_db.refresh(test_user)
        assert test_user.parental_leave["2026"] == [3, 5]


class TestAddVacationDay:
    def test_profile_adds_a_vacation_absence(self, user_client, test_db, test_user):
        resp = user_client.post(
            "/profile/vacation/day",
            data={"vacation_date": "2026-07-15"},
            follow_redirects=False,
        )

        assert resp.status_code == 302
        assert _absences(test_db, test_user.id, AbsenceType.VACATION) == [datetime.date(2026, 7, 15)]

    def test_profile_converts_an_existing_absence(self, user_client, test_db, test_user):
        test_db.add(Absence(user_id=test_user.id, date=datetime.date(2026, 7, 15), absence_type=AbsenceType.SICK))
        test_db.commit()

        user_client.post(
            "/profile/vacation/day",
            data={"vacation_date": "2026-07-15"},
            follow_redirects=False,
        )

        assert _absences(test_db, test_user.id, AbsenceType.SICK) == []
        assert _absences(test_db, test_user.id, AbsenceType.VACATION) == [datetime.date(2026, 7, 15)]

    def test_profile_ignores_a_malformed_date(self, user_client, test_db, test_user):
        resp = user_client.post(
            "/profile/vacation/day",
            data={"vacation_date": "not-a-date"},
            follow_redirects=False,
        )

        assert resp.status_code == 302
        assert test_db.query(Absence).count() == 0

    def test_admin_adds_a_vacation_absence(self, admin_client, test_db, test_user):
        resp = admin_client.post(
            f"/admin/vacation/{test_user.id}/days",
            data={"vacation_date": "2026-07-15"},
            follow_redirects=False,
        )

        assert resp.status_code == 303
        assert _absences(test_db, test_user.id, AbsenceType.VACATION) == [datetime.date(2026, 7, 15)]

    def test_admin_ignores_a_malformed_date(self, admin_client, test_db, test_user):
        resp = admin_client.post(
            f"/admin/vacation/{test_user.id}/days",
            data={"vacation_date": "not-a-date"},
            follow_redirects=False,
        )

        assert resp.status_code == 302
        assert test_db.query(Absence).count() == 0


class TestSyncVacationDays:
    def test_profile_adds_and_removes_within_the_year(self, user_client, test_db, test_user):
        test_db.add(Absence(user_id=test_user.id, date=datetime.date(2026, 3, 1), absence_type=AbsenceType.VACATION))
        test_db.add(Absence(user_id=test_user.id, date=datetime.date(2025, 3, 1), absence_type=AbsenceType.VACATION))
        test_db.add(Absence(user_id=test_user.id, date=datetime.date(2026, 4, 1), absence_type=AbsenceType.PARENTAL))
        test_db.commit()

        resp = user_client.post(
            "/profile/vacation/days/sync",
            data={"year": 2026, "dates": "2026-05-01, bogus, 2026-05-02", "parental_dates": "2026-04-01"},
            follow_redirects=False,
        )

        assert resp.status_code == 302
        assert _absences(test_db, test_user.id, AbsenceType.VACATION) == [
            datetime.date(2025, 3, 1),  # other years are untouched
            datetime.date(2026, 5, 1),
            datetime.date(2026, 5, 2),
        ]
        assert _absences(test_db, test_user.id, AbsenceType.PARENTAL) == [datetime.date(2026, 4, 1)]

    def test_profile_empty_dates_clear_the_year(self, user_client, test_db, test_user):
        test_db.add(Absence(user_id=test_user.id, date=datetime.date(2026, 3, 1), absence_type=AbsenceType.VACATION))
        test_db.add(Absence(user_id=test_user.id, date=datetime.date(2026, 3, 2), absence_type=AbsenceType.PARENTAL))
        test_db.commit()

        user_client.post(
            "/profile/vacation/days/sync",
            data={"year": 2026, "dates": "", "parental_dates": ""},
            follow_redirects=False,
        )

        assert test_db.query(Absence).count() == 0

    def test_admin_adds_and_removes_within_the_year(self, admin_client, test_db, test_user):
        test_db.add(Absence(user_id=test_user.id, date=datetime.date(2026, 3, 1), absence_type=AbsenceType.VACATION))
        test_db.commit()

        resp = admin_client.post(
            f"/admin/vacation/{test_user.id}/days/sync",
            data={"year": 2026, "dates": "2026-05-01", "parental_dates": "2026-06-01"},
            follow_redirects=False,
        )

        assert resp.status_code == 303
        assert "2+tillagda,+1+borttagna" in resp.headers["location"]
        assert _absences(test_db, test_user.id, AbsenceType.VACATION) == [datetime.date(2026, 5, 1)]
        assert _absences(test_db, test_user.id, AbsenceType.PARENTAL) == [datetime.date(2026, 6, 1)]

    def test_admin_reports_no_changes(self, admin_client, test_db, test_user):
        resp = admin_client.post(
            f"/admin/vacation/{test_user.id}/days/sync",
            data={"year": 2026, "dates": "", "parental_dates": ""},
            follow_redirects=False,
        )

        assert "Inga+%C3%A4ndringar" in resp.headers["location"]


class TestDeleteVacationDay:
    def test_profile_deletes_own_vacation_day(self, user_client, test_db, test_user):
        absence = Absence(user_id=test_user.id, date=datetime.date(2026, 5, 1), absence_type=AbsenceType.VACATION)
        test_db.add(absence)
        test_db.commit()

        resp = user_client.post(f"/profile/vacation/day/{absence.id}/delete", follow_redirects=False)

        assert resp.status_code == 302
        assert resp.headers["location"] == "/profile/vacation?year=2026"
        assert test_db.query(Absence).count() == 0

    def test_profile_cannot_delete_another_users_day(self, user_client, test_db, test_user, admin_user):
        absence = Absence(user_id=admin_user.id, date=datetime.date(2026, 5, 1), absence_type=AbsenceType.VACATION)
        test_db.add(absence)
        test_db.commit()

        resp = user_client.post(f"/profile/vacation/day/{absence.id}/delete", follow_redirects=False)

        assert resp.status_code == 302
        assert test_db.query(Absence).count() == 1

    def test_profile_ignores_a_non_vacation_absence(self, user_client, test_db, test_user):
        absence = Absence(user_id=test_user.id, date=datetime.date(2026, 5, 1), absence_type=AbsenceType.SICK)
        test_db.add(absence)
        test_db.commit()

        user_client.post(f"/profile/vacation/day/{absence.id}/delete", follow_redirects=False)

        assert test_db.query(Absence).count() == 1

    def test_admin_deletes_a_users_vacation_day(self, admin_client, test_db, test_user):
        absence = Absence(user_id=test_user.id, date=datetime.date(2026, 5, 1), absence_type=AbsenceType.VACATION)
        test_db.add(absence)
        test_db.commit()

        resp = admin_client.post(
            f"/admin/vacation/{test_user.id}/days/{absence.id}/delete",
            follow_redirects=False,
        )

        assert resp.status_code == 303
        assert f"/admin/vacation/{test_user.id}?year=2026" in resp.headers["location"]
        assert test_db.query(Absence).count() == 0

    def test_admin_cannot_delete_across_users(self, admin_client, test_db, test_user, admin_user):
        absence = Absence(user_id=admin_user.id, date=datetime.date(2026, 5, 1), absence_type=AbsenceType.VACATION)
        test_db.add(absence)
        test_db.commit()

        admin_client.post(f"/admin/vacation/{test_user.id}/days/{absence.id}/delete", follow_redirects=False)

        assert test_db.query(Absence).count() == 1


class TestVacationSettings:
    def test_profile_sets_and_clears_employment_start_date(self, user_client, test_db, test_user):
        resp = user_client.post(
            "/profile/vacation/settings",
            data={"employment_start_date": "2020-02-03"},
            follow_redirects=False,
        )

        assert resp.status_code == 302
        test_db.refresh(test_user)
        assert test_user.employment_start_date == datetime.date(2020, 2, 3)

        user_client.post("/profile/vacation/settings", data={"employment_start_date": ""}, follow_redirects=False)

        test_db.refresh(test_user)
        assert test_user.employment_start_date is None

    def test_profile_keeps_the_date_on_malformed_input(self, user_client, test_db, test_user):
        test_user.employment_start_date = datetime.date(2020, 2, 3)
        test_db.commit()

        user_client.post(
            "/profile/vacation/settings",
            data={"employment_start_date": "nope"},
            follow_redirects=False,
        )

        test_db.refresh(test_user)
        assert test_user.employment_start_date == datetime.date(2020, 2, 3)

    def test_admin_sets_all_vacation_settings(self, admin_client, test_db, test_user):
        resp = admin_client.post(
            f"/admin/vacation/{test_user.id}/settings",
            data={
                "employment_start_date": "2020-02-03",
                "vacation_year_start_month": 4,
                "vacation_days_per_year": 30,
            },
            follow_redirects=False,
        )

        assert resp.status_code == 303
        test_db.refresh(test_user)
        assert test_user.employment_start_date == datetime.date(2020, 2, 3)
        assert test_user.vacation_year_start_month == 4
        assert test_user.vacation_days_per_year == 30

    def test_admin_rejects_out_of_range_settings(self, admin_client, test_db, test_user):
        test_user.vacation_year_start_month = 4
        test_user.vacation_days_per_year = 25
        test_db.commit()

        admin_client.post(
            f"/admin/vacation/{test_user.id}/settings",
            data={
                "employment_start_date": "",
                "vacation_year_start_month": 13,
                "vacation_days_per_year": 99,
            },
            follow_redirects=False,
        )

        test_db.refresh(test_user)
        assert test_user.vacation_year_start_month == 4
        assert test_user.vacation_days_per_year == 25


class TestVacationPages:
    def test_profile_vacation_page_renders(self, user_client, test_db, test_user):
        test_user.vacation = {"2026": [30, 28]}
        test_db.commit()

        resp = user_client.get("/profile/vacation?year=2026")

        assert resp.status_code == 200

    def test_admin_vacation_user_page_renders(self, admin_client, test_db, test_user):
        test_user.vacation = {"2026": [30, 28]}
        test_db.commit()

        resp = admin_client.get(f"/admin/vacation/{test_user.id}?year=2026")

        assert resp.status_code == 200

    def test_admin_vacation_page_unknown_user_redirects(self, admin_client):
        resp = admin_client.get("/admin/vacation/9999", follow_redirects=False)

        assert resp.status_code == 302
        assert resp.headers["location"] == "/admin/vacation"


class TestAdminGate:
    def test_plain_user_cannot_reach_the_admin_vacation_routes(self, user_client, test_db, test_user):
        resp = user_client.post(
            f"/admin/vacation/{test_user.id}/weeks",
            data={"year": 2026, "weeks": "10"},
            follow_redirects=False,
        )

        assert resp.status_code in (302, 303, 401, 403)
        test_db.refresh(test_user)
        assert not (test_user.vacation or {}).get("2026")
