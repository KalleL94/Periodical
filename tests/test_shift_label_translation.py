"""Shift names come from shift_types.json, which is data and holds Swedish only.

Rendering that raw puts "Dagpass" on an English page. t.shift_labels is the
lookup that fixes it, and it has to cover every code the data file defines.
"""

import datetime
import json
import sys
from pathlib import Path

import pytest

import app.database.database as db_module
from app.auth.auth import create_access_token
from app.core.schedule import clear_schedule_cache
from app.core.translations import TRANSLATIONS
from app.database.database import RotationEra, User, UserRole, WageType
from tests.conftest import _ROTATION_ERA_PATTERN

sys.path.insert(0, str(Path(__file__).parent.parent))

DAY = datetime.date(2026, 3, 2)

# ETC is deliberately absent: its label is free text the user typed, so it must
# fall through the lookup untranslated.
UNTRANSLATED_CODES = {"ETC"}


def _shift_codes():
    return {s["code"] for s in json.loads(Path("data/shift_types.json").read_text())}


@pytest.mark.parametrize("lang", ["sv", "en"])
def test_every_shift_code_has_a_label(lang):
    labels = TRANSLATIONS[lang]["shift_labels"]
    missing = _shift_codes() - set(labels) - UNTRANSLATED_CODES
    assert not missing, f"{lang} lacks shift_labels for {sorted(missing)}"


def test_the_two_languages_cover_the_same_codes():
    assert set(TRANSLATIONS["sv"]["shift_labels"]) == set(TRANSLATIONS["en"]["shift_labels"])


def test_etc_is_not_in_the_lookup():
    """A custom block's label is the user's own text, never a translated string."""
    for lang in ("sv", "en"):
        assert "ETC" not in TRANSLATIONS[lang]["shift_labels"]


@pytest.fixture()
def env(test_db, test_client, monkeypatch):
    # A real sessionmaker, not `lambda: test_db`. The month and year routes open
    # several sessions and close them; handing every caller the one fixture
    # session detaches the User the next caller tries to lazy-load.
    from sqlalchemy.orm import sessionmaker

    monkeypatch.setattr(db_module, "SessionLocal", sessionmaker(bind=test_db.get_bind()))
    clear_schedule_cache()
    test_db.query(RotationEra).delete()
    test_db.add(
        RotationEra(
            start_date=datetime.date(2026, 1, 2),
            end_date=None,
            rotation_length=10,
            weeks_pattern=_ROTATION_ERA_PATTERN,
        )
    )
    test_db.add(
        User(
            id=1,
            username="u1",
            password_hash="x",
            name="User 1",
            role=UserRole.USER,
            wage=30000,
            wage_type=WageType.MONTHLY,
            vacation={},
            must_change_password=0,
            is_active=1,
            person_id=1,
            language="en",
        )
    )
    test_db.commit()
    test_client.cookies.set("access_token", create_access_token(data={"sub": "1"}))
    yield test_client, test_db
    clear_schedule_cache()


SWEDISH_SHIFT_NAMES = ("Dagpass", "Kvällspass", "Nattpass")

PAGES = [
    f"/day/1/{DAY.year}/{DAY.month}/{DAY.day}",
    "/week/1?year=2026&week=10",
    "/month/1?year=2026&month=3",
    "/range/1?from=2026-03-01&to=2026-03-31",
    "/year/1?year=2026",
    "/cowork/1",
    "/week?year=2026&week=10",
    "/month?year=2026&month=3",
]


@pytest.mark.parametrize("path", PAGES)
def test_no_swedish_shift_names_on_an_english_page(env, path):
    client, _ = env
    resp = client.get(path)
    assert resp.status_code == 200, f"{path} returned {resp.status_code}"
    for swedish in SWEDISH_SHIFT_NAMES:
        assert swedish not in resp.text, f"{swedish} rendered on {path}"
