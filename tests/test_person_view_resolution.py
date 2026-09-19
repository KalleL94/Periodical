"""Every personal view resolves a path parameter to the same name.

The resolve-plus-name-lookup pair was copied into eight handlers and had drifted:
the week view filtered on is_active and returned None on a miss, so the page heading
came out blank; the range view had lost the legacy User.id fallback; and the day view
never tried User.person_id, so one legacy position answered with a different name
depending on which view you opened.

These pin the union behaviour resolve_person_view now gives all of them.
"""

import pytest

from app.database.database import User, UserRole
from app.routes.shared import resolve_person_view


def _make_user(test_db, uid, name, *, person_id=None, is_active=1):
    user = User(
        id=uid,
        username=f"u{uid}",
        password_hash="x",
        name=name,
        role=UserRole.USER,
        wage=30000,
        vacation={},
        must_change_password=0,
        is_active=is_active,
        person_id=person_id,
    )
    test_db.add(user)
    test_db.commit()
    return user


class TestResolvesAsUser:
    def test_a_user_id_resolves_to_that_user(self, test_db):
        anna = _make_user(test_db, 11, "Anna", person_id=3)

        target, position, wage_uid, name = resolve_person_view(test_db, None, 11)

        assert target is anna
        assert wage_uid == 11
        assert name == "Anna"
        assert position == 3

    def test_the_users_own_name_wins_over_any_position_holder(self, test_db):
        _make_user(test_db, 11, "Anna", person_id=3)
        _make_user(test_db, 12, "Bert", person_id=3)

        _, _, _, name = resolve_person_view(test_db, None, 12)

        assert name == "Bert"


class TestLegacyPosition:
    """A bare rotation position, with no User row carrying that id."""

    def test_falls_back_to_the_position_holder(self, test_db):
        _make_user(test_db, 11, "Anna", person_id=4)

        target, position, wage_uid, name = resolve_person_view(test_db, None, 4)

        assert target is None
        assert (position, wage_uid) == (4, 4)
        assert name == "Anna"

    def test_an_inactive_holder_still_names_the_position(self, test_db):
        """The week view filtered on is_active and so rendered a blank heading here."""
        _make_user(test_db, 11, "Anna", person_id=4, is_active=0)

        _, _, _, name = resolve_person_view(test_db, None, 4)

        assert name == "Anna"

    def test_falls_back_to_the_legacy_user_id_identity(self, test_db):
        """Legacy rows have user_id == person_id and no person_id column set.

        The range view had lost this branch and answered with the placeholder.
        """
        _make_user(test_db, 7, "Gunnar", person_id=None)

        _, _, _, name = resolve_person_view(test_db, None, 7)

        assert name == "Gunnar"

    def test_a_real_user_row_wins_before_any_position_lookup(self, test_db):
        """id 5 names the user with id 5, even when someone else now holds position 5.

        _resolve_person_param resolves a User row first; the position fallbacks below
        are only reached when no row carries that id.
        """
        _make_user(test_db, 5, "Legacy Five", person_id=None)
        _make_user(test_db, 11, "Current Holder", person_id=5)

        target, _, _, name = resolve_person_view(test_db, None, 5)

        assert target is not None
        assert name == "Legacy Five"

    def test_an_unheld_position_gets_the_placeholder_not_none(self, test_db):
        """Never None: a None here rendered as an empty page heading."""
        _, _, _, name = resolve_person_view(test_db, None, 9)

        assert name == "Person 9"

    def test_the_viewer_sees_their_own_name_at_their_own_position(self, test_db):
        # rotation_person_id is derived: person_id when set, else the user's own id.
        viewer = _make_user(test_db, 11, "Anna", person_id=6)

        _, _, _, name = resolve_person_view(test_db, viewer, 6)

        assert name == "Anna"


@pytest.mark.parametrize("raw_id", [4, 7, 9])
def test_the_name_does_not_depend_on_which_view_asked(test_db, raw_id):
    """Same parameter, same answer, with or without a viewed date."""
    _make_user(test_db, 11, "Anna", person_id=4)
    _make_user(test_db, 7, "Gunnar", person_id=None)

    import datetime

    without_date = resolve_person_view(test_db, None, raw_id)[3]
    with_date = resolve_person_view(test_db, None, raw_id, on_date=datetime.date(2026, 6, 1))[3]

    assert without_date == with_date
