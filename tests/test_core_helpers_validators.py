"""Unit tests for small pure helpers: time_utils, validators and helpers.

These guard authorization rules, date validation and overtime time parsing,
which sit on hot paths (every protected route, every OT calculation).
"""

import datetime
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.core.helpers import (
    can_see_data_for_date,
    can_see_salary,
    contrast_color,
    require_own_or_admin,
    strip_salary_data,
    strip_year_summary,
)
from app.core.time_utils import parse_ot_times
from app.core.validators import validate_date_params, validate_person_id
from app.database.database import PersonHistory, UserRole

# --- time_utils.parse_ot_times -------------------------------------------------


def _ot(start, end):
    return SimpleNamespace(start_time=start, end_time=end)


class TestParseOtTimes:
    def test_string_hh_mm(self):
        start, end = parse_ot_times(_ot("06:00", "14:00"), datetime.date(2026, 1, 1))
        assert start == datetime.datetime(2026, 1, 1, 6, 0)
        assert end == datetime.datetime(2026, 1, 1, 14, 0)

    def test_string_hh_mm_ss(self):
        start, end = parse_ot_times(_ot("06:00:30", "14:00:45"), datetime.date(2026, 1, 1))
        assert start.second == 30
        assert end.second == 45

    def test_time_objects_accepted(self):
        start, end = parse_ot_times(_ot(datetime.time(22, 0), datetime.time(6, 0)), datetime.date(2026, 1, 1))
        # End <= start, so the shift crosses midnight onto the next day.
        assert end == datetime.datetime(2026, 1, 2, 6, 0)
        assert end > start

    def test_empty_string_raises(self):
        with pytest.raises(ValueError, match="empty"):
            parse_ot_times(_ot("", "14:00"), datetime.date(2026, 1, 1))

    def test_invalid_format_raises(self):
        with pytest.raises(ValueError, match="Invalid OT"):
            parse_ot_times(_ot("25:99", "14:00"), datetime.date(2026, 1, 1))

    def test_unsupported_type_raises(self):
        with pytest.raises(ValueError, match="Unsupported"):
            parse_ot_times(_ot(600, "14:00"), datetime.date(2026, 1, 1))


# --- validators ----------------------------------------------------------------


class TestValidatePersonId:
    @pytest.mark.parametrize("pid", [1, 5, 10])
    def test_valid(self, pid):
        assert validate_person_id(pid) == pid

    @pytest.mark.parametrize("pid", [0, 11, -1])
    def test_invalid_raises_404(self, pid):
        with pytest.raises(HTTPException) as exc:
            validate_person_id(pid)
        assert exc.value.status_code == 404


class TestValidateDateParams:
    def test_full_date_returned(self):
        assert validate_date_params(2026, 2, 15) == datetime.date(2026, 2, 15)

    def test_year_month_only_returns_none(self):
        assert validate_date_params(2026, 3, None) is None

    def test_year_only_returns_none(self):
        assert validate_date_params(2026, None, None) is None

    def test_day_without_month_is_400(self):
        with pytest.raises(HTTPException) as exc:
            validate_date_params(2026, None, 5)
        assert exc.value.status_code == 400

    def test_impossible_date_is_400(self):
        with pytest.raises(HTTPException) as exc:
            validate_date_params(2026, 2, 30)
        assert exc.value.status_code == 400

    def test_invalid_month_is_400(self):
        with pytest.raises(HTTPException) as exc:
            validate_date_params(2026, 13, None)
        assert exc.value.status_code == 400


# --- helpers -------------------------------------------------------------------


class TestRequireOwnOrAdmin:
    def test_admin_passes_for_any_target(self):
        admin = SimpleNamespace(role=UserRole.ADMIN, id=99)
        assert require_own_or_admin(admin, target_user_id=1) is None

    def test_owner_passes(self):
        user = SimpleNamespace(role=UserRole.USER, id=7)
        assert require_own_or_admin(user, target_user_id=7) is None

    def test_other_user_forbidden(self):
        user = SimpleNamespace(role=UserRole.USER, id=7)
        with pytest.raises(HTTPException) as exc:
            require_own_or_admin(user, target_user_id=8)
        assert exc.value.status_code == 403


class TestContrastColor:
    def test_empty_defaults_to_white(self):
        assert contrast_color("") == "#fff"

    def test_light_background_gets_black_text(self):
        assert contrast_color("#ffffff") == "#000"

    def test_dark_background_gets_white_text(self):
        assert contrast_color("#000000") == "#fff"

    def test_three_char_hex_is_expanded(self):
        assert contrast_color("#fff") == "#000"

    def test_invalid_hex_defaults_to_white(self):
        assert contrast_color("#zzzzzz") == "#fff"


class TestCanSeeSalary:
    def test_anonymous_denied(self):
        assert can_see_salary(None, 1) is False

    def test_admin_allowed(self):
        admin = SimpleNamespace(role=UserRole.ADMIN, rotation_person_id=None)
        assert can_see_salary(admin, 5) is True

    def test_user_own_position_allowed(self):
        user = SimpleNamespace(role=UserRole.USER, rotation_person_id=3)
        assert can_see_salary(user, 3) is True

    def test_user_other_position_denied(self):
        user = SimpleNamespace(role=UserRole.USER, rotation_person_id=3)
        assert can_see_salary(user, 4) is False


class TestStripSalaryData:
    def test_nulls_out_salary_fields_and_day_breakdowns(self):
        data = {
            "brutto_pay": 100,
            "netto_pay": 80,
            "ob_pay": {"OB1": 5},
            "ob_hours": {"OB1": 2},
            "total_ob": 5,
            "days": [{"ob_pay": {"OB1": 5}, "ob_hours": {"OB1": 2}, "other": "kept"}],
        }
        result = strip_salary_data(data)
        assert result["brutto_pay"] is None
        assert result["netto_pay"] is None
        assert result["ob_pay"] == {}
        assert result["total_ob"] is None
        assert result["days"][0]["ob_pay"] == {}
        assert result["days"][0]["other"] == "kept"
        # Original input must not be mutated.
        assert data["brutto_pay"] == 100

    def test_handles_missing_days_key(self):
        result = strip_salary_data({"brutto_pay": 1, "netto_pay": 1, "total_ob": 1})
        assert "days" not in result


class TestStripYearSummary:
    def test_nulls_out_all_aggregate_fields(self):
        summary = {
            "total_netto": 1,
            "total_brutto": 2,
            "total_ob": 3,
            "avg_netto": 4,
            "avg_brutto": 5,
            "avg_ob": 6,
            "ob_hours_by_code": {"OB1": 1},
            "ob_pay_by_code": {"OB1": 1},
            "total_ob_hours": 10,
            "keep_me": "yes",
        }
        result = strip_year_summary(summary)
        assert all(
            result[k] is None
            for k in ["total_netto", "total_brutto", "total_ob", "avg_netto", "avg_brutto", "avg_ob", "total_ob_hours"]
        )
        assert result["ob_hours_by_code"] == {}
        assert result["keep_me"] == "yes"


class TestCanSeeDataForDate:
    def test_anonymous_denied(self):
        assert can_see_data_for_date(None, 1, datetime.date(2026, 2, 15), None) is False

    def test_admin_allowed_without_db_lookup(self):
        admin = SimpleNamespace(role=UserRole.ADMIN, id=99)
        # Passing None as session is fine: admins short-circuit before any query.
        assert can_see_data_for_date(admin, 6, datetime.date(2026, 2, 15), None) is True

    def test_user_who_held_position_allowed(self, test_db):
        test_db.add(
            PersonHistory(
                user_id=6,
                person_id=6,
                name="Kalle",
                username="kalle",
                is_active=1,
                effective_from=datetime.date(2026, 1, 2),
                effective_to=datetime.date(2026, 3, 31),
            )
        )
        test_db.commit()
        user = SimpleNamespace(role=UserRole.USER, id=6)
        assert can_see_data_for_date(user, 6, datetime.date(2026, 2, 15), test_db) is True

    def test_user_outside_employment_window_denied(self, test_db):
        test_db.add(
            PersonHistory(
                user_id=6,
                person_id=6,
                name="Kalle",
                username="kalle",
                is_active=1,
                effective_from=datetime.date(2026, 1, 2),
                effective_to=datetime.date(2026, 3, 31),
            )
        )
        test_db.commit()
        user = SimpleNamespace(role=UserRole.USER, id=6)
        # No PersonHistory record covers May, so access is denied.
        assert can_see_data_for_date(user, 6, datetime.date(2026, 5, 1), test_db) is False
