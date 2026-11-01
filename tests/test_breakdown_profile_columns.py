"""On-call columns are grouped through one map, labelled per employer profile."""

import datetime

import pytest

from app.core.oncall import ONCALL_COLUMN_LABELS, oncall_hours_by_group, oncall_pay_by_group
from app.core.schedule import clear_schedule_cache
from app.database.database import OnCallOverride, OnCallOverrideType, PersonHistory, RotationEra, User
from tests.conftest import _ROTATION_ERA_PATTERN
from tests.test_employer_profile import _login


def test_ica_breakdown_is_grouped():
    bd = {
        "OC_WEEKDAY": {"hours": 10, "pay": 750},
        "OC_WEEKEND_SAT": {"hours": 4, "pay": 388},
        "OC_WEEKEND_MON": {"hours": 2, "pay": 194},
        "OC_HOLIDAY_EVE": {"hours": 7, "pay": 784},
        "OC_SPECIAL": {"hours": 1, "pay": 192},
    }
    assert oncall_hours_by_group(bd) == {"oc_vardag": 10, "oc_helg": 6, "oc_helgdag": 7, "oc_storhelg": 1}
    assert oncall_pay_by_group(bd) == {"oc_vardag": 750, "oc_helg": 582, "oc_helgdag": 784, "oc_storhelg": 192}


def test_nexer_breakdown_is_grouped():
    bd = {"NX_WEEKDAY": {"hours": 18, "pay": 100}, "NX_NIGHT": {"hours": 6, "pay": 50}}
    assert oncall_hours_by_group(bd) == {"oc_vardag": 18, "oc_helg": 6, "oc_helgdag": 0, "oc_storhelg": 0}
    assert oncall_pay_by_group(bd)["oc_helg"] == 50


def test_empty_breakdown_gives_zero_for_every_group():
    assert oncall_hours_by_group({}) == {"oc_vardag": 0, "oc_helg": 0, "oc_helgdag": 0, "oc_storhelg": 0}


def test_labels_per_profile():
    assert ONCALL_COLUMN_LABELS["ica"]["oc_helg"] == "B.Helg(97)"
    assert ONCALL_COLUMN_LABELS["agiremus"] == ONCALL_COLUMN_LABELS["ica"]
    assert ONCALL_COLUMN_LABELS["experis"]["oc_storhelg"] == "B.Storhelg"
    assert ONCALL_COLUMN_LABELS["nexer"]["oc_helg"] == "B.Natt"


@pytest.fixture()
def oc_user(test_db, test_user):
    clear_schedule_cache()
    test_db.add(
        RotationEra(
            start_date=datetime.date(2026, 1, 2),
            end_date=None,
            rotation_length=10,
            weeks_pattern=_ROTATION_ERA_PATTERN,
        )
    )
    test_user.person_id = 1
    test_db.add(OnCallOverride(user_id=1, date=datetime.date(2026, 5, 8), override_type=OnCallOverrideType.ADD))
    test_db.commit()
    yield test_user
    clear_schedule_cache()


def _month(client, test_db, profile):
    test_db.query(User).filter_by(id=1).update({"employer_profile": profile})
    test_db.commit()
    clear_schedule_cache()
    _login(client, "testuser", "testpass123")
    r = client.get("/month/1?year=2026&month=5")
    assert r.status_code == 200
    return r.text


def test_nexer_month_shows_night_column_and_hours(test_client, test_db, oc_user):
    html = _month(test_client, test_db, "nexer")
    assert "B.Natt" in html
    assert "B.Helg(97)" not in html
    # Friday 8 May: 18 h weekday tier, 6 h night tier
    assert ">18.0<" in html
    assert ">6.0<" in html


def test_ica_month_keeps_its_labels(test_client, test_db, oc_user):
    html = _month(test_client, test_db, "ica")
    for label in ("B.Vardag(75)", "B.Helg(97)", "B.Helgdag(112)", "B.Storhelg(192)"):
        assert label in html
    assert "B.Natt" not in html


def test_range_view_uses_the_profile_labels(test_client, test_db, oc_user, admin_user):
    from app.auth.auth import create_access_token

    test_db.query(User).filter_by(id=1).update({"employer_profile": "nexer"})
    test_db.add(
        PersonHistory(
            user_id=1,
            person_id=1,
            name="Test User",
            username="testuser",
            is_active=True,
            effective_from=datetime.date(2026, 1, 2),
        )
    )
    test_db.commit()
    clear_schedule_cache()
    test_client.cookies.set("access_token", f"Bearer {create_access_token(data={'sub': str(admin_user.id)})}")
    r = test_client.get("/range/1?weeks=3&from=2026-07-01")
    assert r.status_code == 200
    assert "B.Natt" in r.text


def test_experis_month_has_its_own_friday_evening_column(test_client, test_db, oc_user):
    assert ">OB1F</th>" in _month(test_client, test_db, "experis")
    assert ">OB1F</th>" not in _month(test_client, test_db, "ica")


def test_experis_month_summary_has_a_friday_evening_cell(test_client, test_db, oc_user):
    assert "Fredag kväll (OB1F)" in _month(test_client, test_db, "experis")
    assert "Fredag kväll (OB1F)" not in _month(test_client, test_db, "ica")


def test_wage_codes_in_the_month_summary_are_ica_only(test_client, test_db, oc_user):
    assert "(OB1/150?)" in _month(test_client, test_db, "ica")
    html = _month(test_client, test_db, "experis")
    assert "(OB1/150?)" not in html
    assert "(OB1)" in html


@pytest.mark.parametrize("path", ["/day/1/2026/5/8", "/year/1?year=2026", "/month/1/payslip?year=2026&month=5"])
def test_wage_codes_are_ica_only_on_every_page(test_client, test_db, oc_user, path):
    import re

    def codes(profile):
        test_db.query(User).filter_by(id=1).update({"employer_profile": profile})
        test_db.commit()
        clear_schedule_cache()
        _login(test_client, "testuser", "testpass123")
        r = test_client.get(path)
        assert r.status_code == 200
        return re.findall(r"\(15[0-3]\)|/15[0-3]", r.text)

    assert codes("experis") == []


def test_x_factors_are_agiremus_only(test_client, test_db, oc_user):
    assert "OB1(x1,12)" in _month(test_client, test_db, "agiremus")
    for profile in ("ica", "experis", "nexer"):
        html = _month(test_client, test_db, profile)
        assert "(x1," not in html
        assert "(x2)" not in html
