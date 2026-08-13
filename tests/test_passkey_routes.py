# tests/test_passkey_routes.py
"""Tests for the passkey model and the passkey HTTP routes."""

import hashlib
import json

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec

from app.auth import webauthn
from app.auth.auth import create_access_token
from app.auth.webauthn import CHALLENGE_COOKIE_NAME
from app.database.database import Passkey

# TestClient issues requests against http://testserver, so that is the RP ID and
# origin the routes will derive and the browser stand-in must claim. "testserver"
# is not localhost, so verify_client_data would reject a plain http origin. The
# tests therefore patch the one predicate that encodes the secure-context rule;
# the rule itself stays under test in tests/test_webauthn.py, where a real
# hostname can be used.
TEST_RP_ID = "testserver"
TEST_ORIGIN = "http://testserver"

FLAG_UP = 0x01
FLAG_UV = 0x04
FLAG_AT = 0x40


@pytest.fixture(autouse=True)
def allow_testserver_origin(monkeypatch):
    """Treat http://testserver as a secure context for the duration of a test."""
    monkeypatch.setattr(
        webauthn,
        "_origin_is_secure",
        lambda parsed: parsed.hostname in ("testserver", "localhost"),
    )


def _login(client, user):
    """Give the client an authenticated session cookie for `user`."""
    client.cookies.set("access_token", f"Bearer {create_access_token(data={'sub': str(user.id)})}")


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


def test_login_options_sets_a_challenge_cookie(test_client):
    """Requesting login options works unauthenticated and seeds the challenge."""
    response = test_client.post("/passkey/login/options")

    assert response.status_code == 200
    body = response.json()
    assert body["userVerification"] == "required"
    assert body["allowCredentials"] == []
    assert len(body["challenge"]) > 0
    assert CHALLENGE_COOKIE_NAME in response.cookies


def test_register_options_requires_a_logged_in_user(test_client):
    response = test_client.post("/passkey/register/options", follow_redirects=False)

    assert response.status_code in (302, 401)


def test_register_options_describes_the_user(test_client, test_user):
    _login(test_client, test_user)

    response = test_client.post("/passkey/register/options")

    assert response.status_code == 200
    body = response.json()
    assert body["user"]["id"] == str(test_user.id)
    assert body["user"]["name"] == test_user.username
    assert body["authenticatorSelection"]["residentKey"] == "required"
    assert body["authenticatorSelection"]["userVerification"] == "required"
    assert body["attestation"] == "none"
    assert {"alg": -7, "type": "public-key"} in body["pubKeyCredParams"]
    assert {"alg": -257, "type": "public-key"} in body["pubKeyCredParams"]


def test_registering_a_passkey_stores_it(test_client, test_db, test_user):
    """The register route verifies the attestation and writes the row."""
    _login(test_client, test_user)
    options = test_client.post("/passkey/register/options").json()

    private = ec.generate_private_key(ec.SECP256R1())
    client_data = _client_data("webauthn.create", options["challenge"], TEST_ORIGIN)
    attestation = _attestation_object(private, b"new-credential")

    response = test_client.post(
        "/passkey/register",
        data={
            "name": "Min telefon",
            "credential": json.dumps(
                {
                    "id": webauthn.b64url_encode(b"new-credential"),
                    "response": {
                        "clientDataJSON": webauthn.b64url_encode(client_data),
                        "attestationObject": webauthn.b64url_encode(attestation),
                    },
                }
            ),
        },
        headers={"Origin": TEST_ORIGIN},
    )

    assert response.status_code == 200
    stored = test_db.query(Passkey).one()
    assert stored.user_id == test_user.id
    assert stored.name == "Min telefon"
    assert stored.credential_id == webauthn.b64url_encode(b"new-credential")


def test_registering_a_passkey_with_a_stale_challenge_is_rejected(test_client, test_db, test_user):
    """Without a challenge cookie there is nothing to verify against."""
    _login(test_client, test_user)
    options = test_client.post("/passkey/register/options").json()
    test_client.cookies.delete(CHALLENGE_COOKIE_NAME)

    private = ec.generate_private_key(ec.SECP256R1())
    client_data = _client_data("webauthn.create", options["challenge"], TEST_ORIGIN)
    attestation = _attestation_object(private, b"new-credential")

    response = test_client.post(
        "/passkey/register",
        data={
            "credential": json.dumps(
                {
                    "id": webauthn.b64url_encode(b"new-credential"),
                    "response": {
                        "clientDataJSON": webauthn.b64url_encode(client_data),
                        "attestationObject": webauthn.b64url_encode(attestation),
                    },
                }
            ),
        },
        headers={"Origin": TEST_ORIGIN},
    )

    assert response.status_code == 400
    assert test_db.query(Passkey).count() == 0


def test_login_with_a_registered_passkey_sets_the_auth_cookie(test_client, test_db, test_user):
    """A full round trip: store a credential, then sign in with it."""
    private, credential_id, _ = _register_credential(test_db, test_user)

    options = test_client.post("/passkey/login/options").json()
    client_data = _client_data("webauthn.get", options["challenge"], TEST_ORIGIN)
    auth_data = _auth_data(FLAG_UP | FLAG_UV, 1)
    signature = _sign(private, auth_data, client_data)

    response = test_client.post(
        "/passkey/login",
        data={"credential": _credential_json(credential_id, client_data, auth_data, signature)},
        headers={"Origin": TEST_ORIGIN},
        follow_redirects=False,
    )

    assert response.status_code == 200
    assert response.json()["redirect"] == "/"
    assert "access_token" in response.cookies

    stored = test_db.query(Passkey).one()
    assert stored.sign_count == 1
    assert stored.last_used_at is not None


def test_login_with_an_unknown_credential_is_rejected(test_client, test_db, test_user):
    private, _, _ = _register_credential(test_db, test_user)

    options = test_client.post("/passkey/login/options").json()
    client_data = _client_data("webauthn.get", options["challenge"], TEST_ORIGIN)
    auth_data = _auth_data(FLAG_UP | FLAG_UV, 1)
    signature = _sign(private, auth_data, client_data)

    response = test_client.post(
        "/passkey/login",
        data={"credential": _credential_json("bm90LXJlZ2lzdGVyZWQ", client_data, auth_data, signature)},
        headers={"Origin": TEST_ORIGIN},
    )

    assert response.status_code == 401
    assert "access_token" not in response.cookies


def test_login_with_a_forged_signature_is_rejected(test_client, test_db, test_user):
    _, credential_id, _ = _register_credential(test_db, test_user)

    options = test_client.post("/passkey/login/options").json()
    client_data = _client_data("webauthn.get", options["challenge"], TEST_ORIGIN)
    auth_data = _auth_data(FLAG_UP | FLAG_UV, 1)
    signature = _sign(ec.generate_private_key(ec.SECP256R1()), auth_data, client_data)

    response = test_client.post(
        "/passkey/login",
        data={"credential": _credential_json(credential_id, client_data, auth_data, signature)},
        headers={"Origin": TEST_ORIGIN},
    )

    assert response.status_code == 401
    assert "access_token" not in response.cookies


def test_passkey_login_still_forces_a_pending_password_change(test_client, test_db, test_user):
    """A passkey does not let a user skip a mandatory password change."""
    test_user.must_change_password = 1
    test_db.commit()
    private, credential_id, _ = _register_credential(test_db, test_user)

    options = test_client.post("/passkey/login/options").json()
    client_data = _client_data("webauthn.get", options["challenge"], TEST_ORIGIN)
    auth_data = _auth_data(FLAG_UP | FLAG_UV, 1)
    signature = _sign(private, auth_data, client_data)

    response = test_client.post(
        "/passkey/login",
        data={"credential": _credential_json(credential_id, client_data, auth_data, signature)},
        headers={"Origin": TEST_ORIGIN},
    )

    assert response.status_code == 200
    assert response.json()["redirect"] == "/change-password"


def test_deleting_a_passkey_removes_it(test_client, test_db, test_user):
    _register_credential(test_db, test_user)
    passkey_id = test_db.query(Passkey).one().id
    _login(test_client, test_user)

    response = test_client.post(f"/profile/passkey/{passkey_id}/delete", follow_redirects=False)

    assert response.status_code == 302
    assert test_db.query(Passkey).count() == 0


def test_cannot_delete_another_users_passkey(test_client, test_db, test_user):
    from app.database.database import User, UserRole

    other = User(
        username="otheruser",
        password_hash="x",
        name="Other",
        role=UserRole.USER,
        wage=30000,
        vacation={},
        must_change_password=0,
    )
    test_db.add(other)
    test_db.commit()
    passkey = Passkey(user_id=other.id, credential_id="b3RoZXI", public_key="a2V5", sign_count=0, name="Other")
    test_db.add(passkey)
    test_db.commit()
    _login(test_client, test_user)

    response = test_client.post(f"/profile/passkey/{passkey.id}/delete", follow_redirects=False)

    assert response.status_code == 404
    assert test_db.query(Passkey).count() == 1


# --- helpers standing in for a browser and an authenticator ---


def _client_data(data_type: str, challenge: str, origin: str) -> bytes:
    return json.dumps({"type": data_type, "challenge": challenge, "origin": origin}).encode("utf-8")


def _auth_data(flags: int, sign_count: int) -> bytes:
    return hashlib.sha256(TEST_RP_ID.encode()).digest() + bytes([flags]) + sign_count.to_bytes(4, "big")


def _sign(private, auth_data: bytes, client_data: bytes) -> bytes:
    return private.sign(auth_data + hashlib.sha256(client_data).digest(), ec.ECDSA(hashes.SHA256()))


def _register_credential(db, user):
    """Insert a Passkey row backed by a real P-256 key. Returns (private, cred id, public key)."""
    private = ec.generate_private_key(ec.SECP256R1())
    numbers = private.public_key().public_numbers()
    cose = _es256_cose(numbers.x, numbers.y)
    credential_id = webauthn.b64url_encode(b"test-credential")
    public_key = webauthn.b64url_encode(cose)

    db.add(
        Passkey(
            user_id=user.id,
            credential_id=credential_id,
            public_key=public_key,
            sign_count=0,
            name="Testnyckel",
        )
    )
    db.commit()
    return private, credential_id, public_key


def _credential_json(credential_id: str, client_data: bytes, auth_data: bytes, signature: bytes) -> str:
    """Serialise a credential the way passkey.js does."""
    return json.dumps(
        {
            "id": credential_id,
            "response": {
                "clientDataJSON": webauthn.b64url_encode(client_data),
                "authenticatorData": webauthn.b64url_encode(auth_data),
                "signature": webauthn.b64url_encode(signature),
            },
        }
    )


def _cbor_uint(major: int, value: int) -> bytes:
    prefix = major << 5
    if value < 24:
        return bytes([prefix | value])
    if value < 256:
        return bytes([prefix | 24, value])
    if value < 65536:
        return bytes([prefix | 25]) + value.to_bytes(2, "big")
    return bytes([prefix | 26]) + value.to_bytes(4, "big")


def _cbor_int(value: int) -> bytes:
    if value >= 0:
        return _cbor_uint(0, value)
    return _cbor_uint(1, -value - 1)


def _cbor_bytes(value: bytes) -> bytes:
    return _cbor_uint(2, len(value)) + value


def _cbor_text(value: str) -> bytes:
    raw = value.encode("utf-8")
    return _cbor_uint(3, len(raw)) + raw


def _es256_cose(x: int, y: int) -> bytes:
    return _cbor_uint(5, 5) + b"".join(
        [
            _cbor_int(1) + _cbor_int(2),
            _cbor_int(3) + _cbor_int(-7),
            _cbor_int(-1) + _cbor_int(1),
            _cbor_int(-2) + _cbor_bytes(x.to_bytes(32, "big")),
            _cbor_int(-3) + _cbor_bytes(y.to_bytes(32, "big")),
        ]
    )


def _attestation_object(private, credential_id: bytes) -> bytes:
    """Build an attestationObject with fmt "none" for a freshly generated key."""
    numbers = private.public_key().public_numbers()
    cose = _es256_cose(numbers.x, numbers.y)
    attested = b"\x00" * 16 + len(credential_id).to_bytes(2, "big") + credential_id + cose
    auth_data = _auth_data(FLAG_UP | FLAG_UV | FLAG_AT, 0) + attested
    return _cbor_uint(5, 3) + b"".join(
        [
            _cbor_text("fmt") + _cbor_text("none"),
            _cbor_text("attStmt") + _cbor_uint(5, 0),
            _cbor_text("authData") + _cbor_bytes(auth_data),
        ]
    )


def test_profile_page_lists_registered_passkeys(test_client, test_db, test_user):
    _register_credential(test_db, test_user)
    _login(test_client, test_user)

    response = test_client.get("/profile")

    assert response.status_code == 200
    assert "Testnyckel" in response.text
    assert "/static/js/passkey.js" in response.text
