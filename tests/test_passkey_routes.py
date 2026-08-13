# tests/test_passkey_routes.py
"""Tests for the passkey model and the passkey HTTP routes."""

from app.database.database import Passkey


def test_passkey_row_round_trips(test_db, test_user):
    """A passkey row stores and reloads with its user link intact."""
    passkey = Passkey(
        user_id=test_user.id,
        credential_id="Y3JlZC1pZA",
        public_key="cHVibGljLWtleQ",
        sign_count=0,
        name="Telefon",
    )
    test_db.add(passkey)
    test_db.commit()

    stored = test_db.query(Passkey).filter(Passkey.credential_id == "Y3JlZC1pZA").one()
    assert stored.user_id == test_user.id
    assert stored.sign_count == 0
    assert stored.name == "Telefon"
    assert stored.created_at is not None
    assert stored.last_used_at is None
    assert stored.user.username == "testuser"
