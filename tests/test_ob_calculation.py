# tests\test_ob_calculation.py
"""
Unit tests for OB (special pay) calculation.

Tests verify that shifts are assigned correct OB hour amounts and pay
according to the Swedish labor rules defined in the project.
"""

import datetime
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Add the app directory to the path so we can import schedule, models, etc.
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

# ruff: noqa: E402
# These imports must come after sys.path.insert() to find the app module
from app.core.holidays import (
    annandagpask,
    first_weekday_after,
    julafton,
    langfredagen,
    midsommarafton,
    skartorsdagen,
)
from app.core.schedule import (
    _cached_special_rules,
    build_special_ob_rules_for_year,
    calculate_ob_hours,
    calculate_ob_pay,
    calculate_shift_hours,
    clear_schedule_cache,
    determine_shift_for_date,
)
from app.core.storage import load_ob_rules, load_settings
from app.database.database import Base, RotationEra

# Use uniquely named in-memory SQLite database for tests (isolated, fast, auto-cleaned)
TEST_DATABASE_URL = "sqlite:///file:test_ob_calc_memdb?mode=memory&cache=shared&uri=true"
test_engine = create_engine(TEST_DATABASE_URL, connect_args={"check_same_thread": False, "uri": True})
TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)


@pytest.fixture(scope="function", autouse=True)
def setup_rotation_era(monkeypatch):
    """Set up rotation era for all tests in this module using an in-memory test database."""
    # Create all tables in memory
    Base.metadata.create_all(bind=test_engine)

    # Create test session
    db = TestSessionLocal()

    # Use monkeypatch to temporarily replace SessionLocal
    import app.database.database as db_module

    monkeypatch.setattr(db_module, "SessionLocal", TestSessionLocal)

    try:
        # Create the default rotation era matching the current system
        era_pattern = {
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

        era = RotationEra(
            start_date=datetime.date(2026, 1, 2),
            end_date=None,
            rotation_length=10,
            weeks_pattern=era_pattern,
        )
        db.add(era)
        db.commit()
    finally:
        db.close()

    yield

    # Clear cache to prevent test interference
    clear_schedule_cache()

    # Drop all test tables (in-memory DB will be auto-deleted)
    Base.metadata.drop_all(bind=test_engine)
    # monkeypatch automatically restores original value


class TestOBCalculation:
    """Test OB hours and pay calculation for various shifts and dates."""

    def setup_method(self):
        """Load base OB rules before each test."""
        self.ob_rules = load_ob_rules()
        self.settings = load_settings()
        # Rotation starts 2026-01-02
        self.rotation_start = datetime.date(2026, 1, 2)

    def find_person_with_shift_on_date(self, target_code: str, target_date: datetime.date) -> tuple:
        """Find which person (1-10) has the target shift code on the given date.

        Returns (person_id, shift, hours, start, end) or raises AssertionError.
        """
        for person_id in range(1, 11):
            shift, _ = determine_shift_for_date(target_date, start_week=person_id)
            if shift and shift.code == target_code:
                hours, start, end = calculate_shift_hours(target_date, shift)
                return (person_id, shift, hours, start, end)

        raise AssertionError(f"Could not find person with {target_code} shift on {target_date}")

    # -------------------------
    # Debug helpers
    # -------------------------

    def debug_scenario(
        self,
        label,
        date,
        person_id,
        shift,
        hours,
        start,
        end,
        ob_hours,
        ob_pay=None,
        special_rules=None,
    ):
        """Print a detailed description of the test scenario."""
        print(f"    Scenario: {label}")
        print(f"      Date: {date} (weekday {date.weekday()})")
        print(f"      Person: {person_id}")
        if shift:
            print(f"      Shift: {shift.code} {getattr(shift, 'label', '')}".rstrip())
            print(f"      Time:  {start} -> {end}  ({hours:.2f} h)")
        else:
            print("      Shift: OFF or None")
        print("      OB hours:")
        for code in sorted(ob_hours.keys()):
            print(f"        {code}: {ob_hours[code]:.2f}")

        if ob_pay is not None:
            print("      OB pay:")
            for code in sorted(ob_pay.keys()):
                print(f"        {code}: {ob_pay[code]:.2f}")

        if special_rules is not None:
            date_iso = date.isoformat()
            active = [r for r in special_rules if getattr(r, "specific_dates", None) and date_iso in r.specific_dates]
            if active:
                print("      Active special rules for this date:")
                for r in active:
                    print(f"        {r.code} {r.label} {r.start_time}-{r.end_time}")
            else:
                print("      Active special rules for this date: none")

    # -------------------------
    # Tests
    # -------------------------

    def test_regular_weekday_morning_shift_no_ob(self):
        """Morning shift (N1) on a regular weekday should have OB2 from 06:00-07:00 only."""
        test_date = datetime.date(2027, 1, 19)  # Monday of week 3
        person_id, shift, hours, start, end = self.find_person_with_shift_on_date("N1", test_date)

        assert shift.code == "N1"
        assert hours == 8.5

        ob_hours = calculate_ob_hours(start, end, self.ob_rules)

        # Debug output
        self.debug_scenario(
            "Weekday morning N1",
            test_date,
            person_id,
            shift,
            hours,
            start,
            end,
            ob_hours,
        )

        # Morning shift 06:00-14:30 has OB2 from 06:00-07:00 (1 hour)
        assert ob_hours["OB2"] == 1.0, f"Expected OB2=1.0, got {ob_hours['OB2']}"
        # No evening OB1 (shift ends at 14:30, before 18:00)
        assert ob_hours["OB1"] == 0.0, f"Expected OB1=0.0, got {ob_hours['OB1']}"

    def test_regular_weekday_evening_shift(self):
        """Evening shift (N2) on a regular weekday should have OB1 from 18:00-22:30."""
        test_date = datetime.date(2027, 1, 26)  # Monday of week 4
        person_id, shift, hours, start, end = self.find_person_with_shift_on_date("N2", test_date)

        assert shift.code == "N2"
        assert hours == 8.5

        ob_hours = calculate_ob_hours(start, end, self.ob_rules)

        # Debug output
        self.debug_scenario(
            "Weekday evening N2",
            test_date,
            person_id,
            shift,
            hours,
            start,
            end,
            ob_hours,
        )

        # Evening shift 14:00-22:30 has 4.5h OB1 (18:00-22:30)
        assert ob_hours["OB1"] == 4.5, f"Expected OB1=4.5, got {ob_hours['OB1']}"
        assert ob_hours["OB2"] == 0.0, f"Expected OB2=0.0, got {ob_hours['OB2']}"

    def test_night_shift_spanning_midnight(self):
        """Night shift (N3) 22:00-06:30 (next day) should include OB1 and OB2."""
        test_date = datetime.date(2026, 1, 5)  # Monday of week 2
        person_id, shift, hours, start, end = self.find_person_with_shift_on_date("N3", test_date)
        print(shift)
        assert shift.code == "N3"
        assert hours == 8.5
        # Verify it spans midnight
        assert end.date() == start.date() + datetime.timedelta(days=1), (
            f"Expected to span midnight but start={start.date()}, end={end.date()}"
        )

        ob_hours = calculate_ob_hours(start, end, self.ob_rules)

        # Debug output
        self.debug_scenario(
            "Night N3 across midnight",
            test_date,
            person_id,
            shift,
            hours,
            start,
            end,
            ob_hours,
        )

        # 22:00-24:00 on first day = OB1 (evening, 2 hours)
        # 00:00-06:30 on next day = OB2 (night, 6.5 hours)
        assert ob_hours["OB1"] == 2.0, f"Expected OB1=2.0, got {ob_hours['OB1']}"
        assert ob_hours["OB2"] == 6.5, f"Expected OB2=6.5, got {ob_hours['OB2']}"

    def test_good_friday_shift(self):
        """Shift on Good Friday should be OB5 (storhelg)."""
        year = 2027
        test_date = langfredagen(year)
        person_id, shift, hours, start, end = self.find_person_with_shift_on_date("N1", test_date)

        shift, _ = determine_shift_for_date(test_date, start_week=person_id)

        assert shift is not None and shift.code != "OFF", "Expected someone to work on Good Friday in rotation"

        hours, start, end = calculate_shift_hours(test_date, shift)

        special_rules = _cached_special_rules(year)
        combined = self.ob_rules + special_rules
        monthly_salary = self.settings.monthly_salary

        ob_hours = calculate_ob_hours(start, end, combined)
        ob_pay = calculate_ob_pay(start, end, combined, monthly_salary)

        # Debug output
        self.debug_scenario(
            "Good Friday storhelg OB5",
            test_date,
            "unknown",  # start_week based, not person_id directly
            shift,
            hours,
            start,
            end,
            ob_hours,
            ob_pay,
            special_rules,
        )

        # All hours on Good Friday should be OB5
        assert ob_hours["OB5"] == hours, f"Expected OB5={hours}, got {ob_hours['OB5']} on Good Friday {test_date}"
        assert ob_hours["OB1"] == 0.0
        assert ob_hours["OB4"] == 0.0

    def test_ob5_easter_monday(self):
        date = annandagpask(2027)  # eller 2026
        start = datetime.datetime.combine(date, datetime.time(14, 0))
        end = datetime.datetime.combine(date, datetime.time(22, 30))
        special = build_special_ob_rules_for_year(date.year)
        combined = self.ob_rules + special
        ob_hours = calculate_ob_hours(start, end, combined)
        assert ob_hours["OB5"] == 8.5

    def test_calculate_ob_pay(self):
        """Test OB pay calculation using monthly salary and rule rates."""
        test_date = datetime.date(2027, 1, 26)  # Monday of week 4
        person_id, shift, hours, start, end = self.find_person_with_shift_on_date("N2", test_date)
        monthly_salary = self.settings.monthly_salary

        ob_pay = calculate_ob_pay(start, end, self.ob_rules, monthly_salary)
        ob_hours = calculate_ob_hours(start, end, self.ob_rules)

        # Debug output
        self.debug_scenario(
            "Pay calculation for N2",
            test_date,
            person_id,
            shift,
            hours,
            start,
            end,
            ob_hours,
            ob_pay,
        )

        # Evening shift 14:00-22:30 has 4.5h OB1
        expected_ob1_pay = 4.5 * (monthly_salary / 600)
        assert ob_pay["OB1"] == expected_ob1_pay, f"Expected OB1 pay={expected_ob1_pay}, got {ob_pay['OB1']}"

    def test_off_shift_no_ob(self):
        """OFF shift should result in zero OB hours and pay."""
        test_date = datetime.date(2027, 1, 5)  # Monday of week 2
        person_id, shift, hours, start, end = self.find_person_with_shift_on_date("OFF", test_date)

        assert shift.code == "OFF"
        assert hours == 0.0
        assert start is None
        assert end is None

        ob_hours = calculate_ob_hours(start, end, self.ob_rules)

        # Debug output
        self.debug_scenario(
            "OFF shift, no OB",
            test_date,
            person_id,
            shift,
            hours,
            start,
            end,
            ob_hours,
        )

        for code in ob_hours:
            assert ob_hours[code] == 0.0, f"Expected {code}=0.0 for OFF shift, got {ob_hours[code]}"

    def test_special_ob_rules_generated(self):
        """Verify that special OB rules are generated for holidays."""
        year = 2027
        special_rules = _cached_special_rules(year)

        assert len(special_rules) > 0, "No special OB rules generated"
        ob4_rules = [r for r in special_rules if r.code == "OB4"]
        ob5_rules = [r for r in special_rules if r.code == "OB5"]
        assert len(ob4_rules) > 0, "No OB4 rules generated"
        assert len(ob5_rules) > 0, "No OB5 rules generated"

    def test_ob4_epiphany(self):
        """Trettondagen (6 januari) ska ge OB4 efter kl 07:00."""
        year = 2027
        date = datetime.date(year, 1, 6)

        pid, shift, hours, start, end = self.find_person_with_shift_on_date("N1", date)
        if shift is None or shift.code == "OFF":
            for code in ["N2", "N3"]:
                try:
                    pid, shift, hours, start, end = self.find_person_with_shift_on_date(code, date)
                    break
                except AssertionError:
                    continue

        special = build_special_ob_rules_for_year(year)
        combined = self.ob_rules + special

        ob_hours = calculate_ob_hours(start, end, combined)

        # Debug output
        self.debug_scenario(
            "Epiphany OB4",
            date,
            pid,
            shift,
            hours,
            start,
            end,
            ob_hours,
            special_rules=special,
        )

        assert ob_hours["OB4"] > 0.0, f"Expected OB4 on Epiphany, got {ob_hours}"
        assert ob_hours["OB5"] == 0.0, f"OB5 should not apply on Epiphany, got {ob_hours['OB5']}"
        assert ob_hours["OB1"] == 0.0, "OB1 should be overridden by OB4 on Epiphany"
        assert ob_hours["OB2"] == 1.0, "OB2 should be overridden by OB4 on Epiphany"

    def test_ob5_new_year_eve(self):
        """Nyårsafton ska ge OB5 på allt efter 18:00."""
        year = 2026
        date = datetime.date(year, 12, 31)

        pid, shift, hours, start, end = self.find_person_with_shift_on_date("N2", date)
        if shift is None or shift.code == "OFF":
            pid, shift, hours, start, end = self.find_person_with_shift_on_date("N3", date)

        special = build_special_ob_rules_for_year(year)
        combined = self.ob_rules + special

        ob_hours = calculate_ob_hours(start, end, combined)

        # Debug output
        self.debug_scenario(
            "New Year Eve OB5",
            date,
            pid,
            shift,
            hours,
            start,
            end,
            ob_hours,
            special_rules=special,
        )

        assert ob_hours["OB5"] > 0.0, f"NYE should give OB5, got {ob_hours}"
        for code in ["OB4", "OB3", "OB2", "OB1"]:
            assert ob_hours[code] == 0.0, f"{code} should not apply on NYE when OB5 applies"

    def test_ob5_new_year_eve_on_weekend(self):
        """Nyårsafton ska ge OB5 på allt efter 18:00."""
        year = 2028
        date = datetime.date(year, 12, 31)

        pid, shift, hours, start, end = self.find_person_with_shift_on_date("N2", date)
        if shift is None or shift.code == "OFF":
            pid, shift, hours, start, end = self.find_person_with_shift_on_date("N3", date)

        special = build_special_ob_rules_for_year(year)
        combined = self.ob_rules + special

        ob_hours = calculate_ob_hours(start, end, combined)

        # Debug output
        self.debug_scenario(
            "New Year Eve OB5 on Weekend",
            date,
            pid,
            shift,
            hours,
            start,
            end,
            ob_hours,
            special_rules=special,
        )

        assert ob_hours["OB5"] > 0.0, f"NYE should give OB5, got {ob_hours}"
        assert ob_hours["OB3"] > 0.0, f"NYE should give OB3 on weekends, got {ob_hours}"
        for code in ["OB4", "OB2", "OB1"]:
            assert ob_hours[code] == 0.0, f"{code} should not apply on NYE when OB5 applies"

    def test_2_jan_on_weekend(self):
        """2 januari ska ge OB5 om det är en lördag eller söndag."""
        year = 2027
        date = datetime.date(year, 1, 2)

        pid, shift, hours, start, end = self.find_person_with_shift_on_date("N2", date)
        if shift is None or shift.code == "OFF":
            pid, shift, hours, start, end = self.find_person_with_shift_on_date("N3", date)

        special = build_special_ob_rules_for_year(year)
        combined = self.ob_rules + special

        ob_hours = calculate_ob_hours(start, end, combined)

        # Debug output
        self.debug_scenario(
            "2nd of January on Weekend",
            date,
            pid,
            shift,
            hours,
            start,
            end,
            ob_hours,
            special_rules=special,
        )

        assert ob_hours["OB5"] > 0.0, f"2nd of January should give OB5, got {ob_hours}"
        for code in ["OB4", "OB3", "OB2", "OB1"]:
            assert ob_hours[code] == 0.0, f"{code} should not apply on 2nd of January when OB5 applies"

    def test_ob5_skartorsdag_evening_shift(self):
        """Skärtorsdagen ska ge OB5 på allt efter kl 18:00."""
        year = 2027
        date = skartorsdagen(year)

        # Försök hitta en kvällstjänst (N2), annars natt (N3)
        pid, shift, hours, start, end = self.find_person_with_shift_on_date("N2", date)
        if shift is None or shift.code == "OFF":
            pid, shift, hours, start, end = self.find_person_with_shift_on_date("N3", date)

        special = build_special_ob_rules_for_year(year)
        combined = self.ob_rules + special

        ob_hours = calculate_ob_hours(start, end, combined)

        # Debug output
        self.debug_scenario(
            "Skärtorsdag storhelg OB5 efter 18",
            date,
            pid,
            shift,
            hours,
            start,
            end,
            ob_hours,
            special_rules=special,
        )

        # Skärtorsdag ska ge OB5 på kvällspasset, övriga OB-typer ska inte få tid
        assert ob_hours["OB5"] > 0.0, f"Skärtorsdag ska ge OB5 på kvällspasset, fick {ob_hours}"
        for code in ["OB4", "OB3", "OB2", "OB1"]:
            assert ob_hours[code] == 0.0, (
                f"{code} ska inte gälla på Skärtorsdagens kvällspass när OB5 gäller, fick {ob_hours[code]}"
            )

    def test_ob5_christmas_eve_evening_shift(self):
        """Julafton ska ligga som storhelg (OB5) från kl 07:00."""
        year = 2027
        date = julafton(year)

        # Försök hitta en kvällstjänst (N2), annars natt (N3)
        pid, shift, hours, start, end = self.find_person_with_shift_on_date("N2", date)
        if shift is None or shift.code == "OFF":
            pid, shift, hours, start, end = self.find_person_with_shift_on_date("N3", date)

        special = build_special_ob_rules_for_year(year)
        combined = self.ob_rules + special

        ob_hours = calculate_ob_hours(start, end, combined)

        # Debug output
        self.debug_scenario(
            "Julafton storhelg OB5 från 07",
            date,
            pid,
            shift,
            hours,
            start,
            end,
            ob_hours,
            special_rules=special,
        )

        # Hela kvälls-/nattpasset på julafton ska ligga inom storhelgsblocket
        assert ob_hours["OB5"] == hours, (
            f"Alla arbetade timmar på julafton ska vara OB5, fick OB5={ob_hours['OB5']} av totalt {hours}"
        )
        for code in ["OB4", "OB3", "OB2", "OB1"]:
            assert ob_hours[code] == 0.0, (
                f"{code} ska inte gälla på julaftons kvälls/nattpass när OB5 gäller, fick {ob_hours[code]}"
            )

    def test_ob5_weekend_after_christmas_block_2031(self):
        """Helgen efter jul 2031 ska fortfarande räknas som storhelg (OB5)."""
        year = 2031
        dates = [
            datetime.date(year, 12, 27),  # lördag
            datetime.date(year, 12, 28),  # söndag
        ]

        special = build_special_ob_rules_for_year(year)

        for date in dates:
            # Försök hitta en kvällstjänst (N2), annars natt (N3)
            pid, shift, hours, start, end = self.find_person_with_shift_on_date("N2", date)
            if shift is None or shift.code == "OFF":
                pid, shift, hours, start, end = self.find_person_with_shift_on_date("N3", date)

            combined = self.ob_rules + special
            ob_hours = calculate_ob_hours(start, end, combined)

            # Debug output
            self.debug_scenario(
                "Helg efter julblock 2031",
                date,
                pid,
                shift,
                hours,
                start,
                end,
                ob_hours,
                special_rules=special,
            )

            # Alla timmar på dessa dagar ska vara OB5
            assert ob_hours["OB5"] == hours, (
                f"Alla arbetade timmar {date} ska vara OB5, fick OB5={ob_hours['OB5']} av totalt {hours}"
            )
            # Ingen annan OB-kod ska ha timmar när OB5 gäller
            for code in ["OB4", "OB3", "OB2", "OB1"]:
                assert ob_hours[code] == 0.0, f"{code} ska inte gälla {date} när OB5 gäller, fick {ob_hours[code]}"

    def test_ob5_ends_on_first_weekday_after_christmas_block_2031(self):
        """Första vardagen efter julblocket 2031 ska inte längre ha OB5."""
        year = 2031
        date = datetime.date(year, 12, 29)  # måndag

        # Ta ett vanligt kvällspass den dagen
        pid, shift, hours, start, end = self.find_person_with_shift_on_date("N2", date)
        if shift is None or shift.code == "OFF":
            pid, shift, hours, start, end = self.find_person_with_shift_on_date("N3", date)

        special = build_special_ob_rules_for_year(year)
        combined = self.ob_rules + special
        ob_hours = calculate_ob_hours(start, end, combined)

        # Debug output
        self.debug_scenario(
            "Första vardagen efter julblock 2031",
            date,
            pid,
            shift,
            hours,
            start,
            end,
            ob_hours,
            special_rules=special,
        )

        # OB5-blocket ska ha slutat vid midnatt, så ingen OB5 denna dag
        assert ob_hours["OB5"] == 0.0, (
            f"Första vardagen efter julblocket ska inte ha OB5, fick OB5={ob_hours['OB5']} för {date}"
        )

    def test_ob5_midsommar_block_weekend(self):
        """Midsommarafton, midsommardagen och midsommarsöndagen ska ligga i samma OB5-block."""
        year = 2027
        eve = midsommarafton(year)
        dates = [
            eve,  # midsommarafton (fredag)
            eve + datetime.timedelta(days=1),  # midsommardagen (lördag)
            eve + datetime.timedelta(days=2),  # midsommarsöndagen (söndag)
        ]

        special = build_special_ob_rules_for_year(year)

        for date in dates:
            # Försök hitta en kvällstjänst (N2), annars natt (N3)
            pid, shift, hours, start, end = self.find_person_with_shift_on_date("N2", date)
            if shift is None or shift.code == "OFF":
                pid, shift, hours, start, end = self.find_person_with_shift_on_date("N1", date)

            combined = self.ob_rules + special
            ob_hours = calculate_ob_hours(start, end, combined)

            # Debug output
            self.debug_scenario(
                "Midsommar storhelgsblock",
                date,
                pid,
                shift,
                hours,
                start,
                end,
                ob_hours,
                special_rules=special,
            )

            # Alla arbetade timmar dessa dagar ska vara OB5
            assert ob_hours["OB5"] == hours, (
                f"Alla arbetade timmar {date} ska vara OB5, fick OB5={ob_hours['OB5']} av totalt {hours}"
            )
            for code in ["OB4", "OB3", "OB2", "OB1"]:
                assert ob_hours[code] == 0.0, f"{code} ska inte gälla {date} när OB5 gäller, fick {ob_hours[code]}"

    def test_ob5_midsommar_block_ends_on_first_weekday(self):
        """Första vardagen efter midsommarblocket ska inte ha OB5."""
        year = 2027
        last_holiday = midsommarafton(year) + datetime.timedelta(days=1)
        first_weekday = first_weekday_after(last_holiday)

        # Ta ett kvälls- eller nattpass den vardagen
        pid, shift, hours, start, end = self.find_person_with_shift_on_date("N2", first_weekday)
        if shift is None or shift.code == "OFF":
            pid, shift, hours, start, end = self.find_person_with_shift_on_date("N3", first_weekday)

        special = build_special_ob_rules_for_year(year)
        combined = self.ob_rules + special
        ob_hours = calculate_ob_hours(start, end, combined)

        # Debug output
        self.debug_scenario(
            "Första vardagen efter midsommarblock",
            first_weekday,
            pid,
            shift,
            hours,
            start,
            end,
            ob_hours,
            special_rules=special,
        )

        # OB5 ska ha tagit slut vid midnatt innan denna dag
        assert ob_hours["OB5"] == 0.0, (
            f"Första vardagen efter midsommarblock ska inte ha OB5, fick OB5={ob_hours['OB5']} för {first_weekday}"
        )

    def test_ob5_override_ob4_everywhere(self):
        """När OB5 gäller ska OB4 aldrig få några timmar."""
        year = 2027
        date = langfredagen(year)

        pid, shift, hours, start, end = self.find_person_with_shift_on_date("N1", date)
        if shift is None or shift.code == "OFF":
            pid, shift, hours, start, end = self.find_person_with_shift_on_date("N2", date)
        if shift is None or shift.code == "OFF":
            pid, shift, hours, start, end = self.find_person_with_shift_on_date("N3", date)

        special = build_special_ob_rules_for_year(year)
        combined = self.ob_rules + special

        ob_hours = calculate_ob_hours(start, end, combined)

        # Debug output
        self.debug_scenario(
            "OB5 overrides OB4 on Good Friday",
            date,
            pid,
            shift,
            hours,
            start,
            end,
            ob_hours,
            special_rules=special,
        )

        assert ob_hours["OB5"] > 0.0, "OB5 should apply on Good Friday"
        assert ob_hours["OB4"] == 0.0, "OB4 must never apply when OB5 applies"
        assert ob_hours["OB3"] == 0.0, "OB3 must be overridden by OB5"
        assert ob_hours["OB2"] == 0.0, "OB2 must be overridden by OB5"
        assert ob_hours["OB1"] == 0.0, "OB1 must be overridden by OB5"

    def test_ob5_weekend_after_new_year_2027(self):
        """Helgen efter nyårsdagen 2027 ska fortfarande vara OB5."""
        year = 2027
        dates = [
            datetime.date(year, 1, 2),  # lördag
            datetime.date(year, 1, 3),  # söndag
        ]

        special = build_special_ob_rules_for_year(year)

        for date in dates:
            # Försök hitta en kvällstjänst (N2), annars natt (N3)
            pid, shift, hours, start, end = self.find_person_with_shift_on_date("N2", date)
            if shift is None or shift.code == "OFF":
                pid, shift, hours, start, end = self.find_person_with_shift_on_date("N3", date)

            combined = self.ob_rules + special
            ob_hours = calculate_ob_hours(start, end, combined)

            self.debug_scenario(
                "Nyårsblock 2027 helg efter nyårsdagen",
                date,
                pid,
                shift,
                hours,
                start,
                end,
                ob_hours,
                special_rules=special,
            )

            assert ob_hours["OB5"] == hours, f"{date} ska vara storhelg (OB5), fick OB5={ob_hours['OB5']} av {hours}"
            for code in ["OB4", "OB3", "OB2", "OB1"]:
                assert ob_hours[code] == 0.0, f"{code} ska inte gälla {date} när OB5 gäller, fick {ob_hours[code]}"

    def test_ob5_new_year_block_ends_on_first_weekday_2027(self):
        """Första vardagen efter nyårsblocket 2027 ska inte ha OB5."""
        year = 2027
        first_weekday = datetime.date(year, 1, 4)  # måndag efter helgen

        pid, shift, hours, start, end = self.find_person_with_shift_on_date("N2", first_weekday)
        if shift is None or shift.code == "OFF":
            pid, shift, hours, start, end = self.find_person_with_shift_on_date("N3", first_weekday)

        special = build_special_ob_rules_for_year(year)
        combined = self.ob_rules + special
        ob_hours = calculate_ob_hours(start, end, combined)

        self.debug_scenario(
            "Första vardagen efter nyårsblock 2027",
            first_weekday,
            pid,
            shift,
            hours,
            start,
            end,
            ob_hours,
            special_rules=special,
        )

        assert ob_hours["OB5"] == 0.0, f"{first_weekday} ska inte ha OB5, men OB5={ob_hours['OB5']}"


class C:
    GREEN = "\033[92m"
    RED = "\033[91m"
    CYAN = "\033[96m"
    YELLOW = "\033[93m"
    END = "\033[0m"


def run_tests():
    print("\n" + "=" * 80)
    print(f"{C.CYAN}Running OB Calculation Tests (SUPER VERBOSE MODE){C.END}")
    print("=" * 80 + "\n")

    test_obj = TestOBCalculation()
    test_methods = [
        method for method in dir(test_obj) if method.startswith("test_") and callable(getattr(test_obj, method))
    ]

    passed = 0
    failed = 0

    for test_name in sorted(test_methods):
        test_obj.setup_method()
        print(f"\n{C.YELLOW}>>> Running {test_name}{C.END}")

        try:
            getattr(test_obj, test_name)()
            print(f"{C.GREEN}    ✓ PASS - {test_name}{C.END}")
            passed += 1

        except AssertionError as e:
            print(f"{C.RED}    ✗ FAIL - {test_name}{C.END}")
            print(f"{C.RED}      AssertionError: {e}{C.END}")
            import traceback

            traceback.print_exc()
            failed += 1

        except Exception as e:
            print(f"{C.RED}    ✗ ERROR - {test_name}{C.END}")
            print(f"{C.RED}      Unexpected error: {e}{C.END}")
            import traceback

            traceback.print_exc()
            failed += 1

    print("\n" + "=" * 80)
    print(f"{C.CYAN}Summary:{C.END} {passed} passed, {failed} failed")
    print("=" * 80 + "\n")

    return failed == 0


if __name__ == "__main__":
    success = run_tests()
    sys.exit(0 if success else 1)
