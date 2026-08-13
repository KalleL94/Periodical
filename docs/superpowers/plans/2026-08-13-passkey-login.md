# Passkey Login Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a user sign in to Periodical with a passkey (WebAuthn) instead of typing a password, while password login keeps working unchanged.

**Architecture:** A self-contained `app/auth/webauthn.py` verifies WebAuthn registration and assertion responses using `cryptography`, which is already a dependency. Challenges are stateless: a signed, expiring cookie, exactly like the existing CSRF token. A new `passkeys` table stores one row per credential. New routes in `app/routes/passkey_routes.py` end by calling the same `create_access_token` / `set_auth_cookie` / `log_auth_event` sequence the password login already uses.

**Tech Stack:** FastAPI, SQLAlchemy, SQLite, Jinja2, vanilla JS, `cryptography` (already installed), pytest.

**Spec:** `docs/superpowers/specs/2026-08-13-passkey-login-design.md`

## Global Constraints

- **No new Python dependencies.** `pyproject.toml` and `requirements.txt` must not change. Only `cryptography` (installed, 46.0.3) and the standard library.
- **All source code comments in English.** No exceptions.
- **No em dash (—) anywhere**: not in code, comments, docs, commit messages or PR text. Use a comma, colon, parentheses or full stop. The en dash (–) stays allowed for numeric ranges only.
- **No AI attribution in commit messages.** No "Generated with", no "Co-Authored-By: Claude".
- **User-facing strings go through `app/core/translations.py`**, added to both the `"sv"` and `"en"` dicts. Never hardcode display text in a template.
- **Every POST form needs the hidden `csrf_token` field** or `CSRFMiddleware` returns 403.
- **Python interpreter is `venv/bin/python3`.** Run tests as `venv/bin/python3 -m pytest`.
- **Before any push:** `ruff check .`, `ruff format --check .` and the pytest suite must all pass. `ruff format --check` is its own CI step.
- **Branch:** `feat/passkey-login`, already created from `origin/main`. Never commit to `main`.
- **Supported COSE algorithms:** ES256 (`-7`) and RS256 (`-257`) only. Everything else is rejected.
- **Challenge cookie name:** `webauthn_challenge`. **TTL:** 300 seconds.

---

## File Structure

| File | Responsibility |
|---|---|
| `app/auth/webauthn.py` | **Create.** CBOR reader, challenge signing, COSE key handling, registration and assertion verification. Pure functions, no FastAPI, no database. |
| `app/database/database.py` | **Modify.** Add the `Passkey` model. |
| `app/routes/passkey_routes.py` | **Create.** HTTP layer: options endpoints, register, login, delete. |
| `app/main.py` | **Modify.** Register the router. |
| `app/core/translations.py` | **Modify.** New `sv` and `en` keys. |
| `app/static/js/passkey.js` | **Create.** Browser glue for `navigator.credentials`. |
| `app/templates/login.html` | **Modify.** Passkey button plus autofill hint. |
| `app/templates/profile.html` | **Modify.** Passkey management section. |
| `app/routes/profile.py` | **Modify.** Pass the user's passkeys into the template context. |
| `tests/test_webauthn.py` | **Create.** Unit tests for the verification module. |
| `tests/test_passkey_routes.py` | **Create.** Route-level tests. |
| `CHANGELOG.md` | **Modify.** Release note. |

`webauthn.py` deliberately knows nothing about FastAPI or SQLAlchemy: it takes bytes and strings and returns dataclasses or raises. That is what makes Task 2's tests cheap to write and lets Task 5's routes stay thin.

---

## Task 1: The `Passkey` model

**Files:**
- Modify: `app/database/database.py` (add the model after the `LoginAttempt` class, around line 615)
- Test: `tests/test_passkey_routes.py`

**Interfaces:**
- Consumes: nothing
- Produces: `Passkey` model importable from `app.database.database`, with columns `id`, `user_id`, `credential_id`, `public_key`, `sign_count`, `name`, `created_at`, `last_used_at`

- [ ] **Step 1: Write the failing test**

Create `tests/test_passkey_routes.py`:

```python
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
```

The fixtures used throughout this plan are the real ones in `tests/conftest.py`:
`test_db` (in-memory session), `test_client` (a `CSRFTestClient` that injects the
CSRF token automatically) and `test_user` (username `testuser`, id 1,
`must_change_password=0`). There is no `client` or `authenticated_client` fixture;
tests that need a logged-in session set the cookie themselves, which Task 5 covers.

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/bin/python3 -m pytest tests/test_passkey_routes.py -v`
Expected: FAIL with `ImportError: cannot import name 'Passkey'`

- [ ] **Step 3: Add the model**

In `app/database/database.py`, after the `LoginAttempt` class and before `def create_tables():`:

```python
class Passkey(Base):
    """A WebAuthn credential registered by a user for passwordless login.

    One row per authenticator. `public_key` holds the raw COSE_Key bytes exactly
    as the authenticator sent them, base64url encoded, so registration and
    assertion verification share a single parser.
    """

    __tablename__ = "passkeys"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    credential_id = Column(String(255), unique=True, nullable=False, index=True)
    public_key = Column(Text, nullable=False)
    # The authenticator's signature counter. Authenticators that do not implement
    # one always send 0, so the replay check only applies once a non-zero value
    # has been seen.
    sign_count = Column(Integer, default=0, nullable=False)
    name = Column(String(100), nullable=False)
    created_at = Column(DateTime, default=utcnow, nullable=False)
    last_used_at = Column(DateTime, nullable=True)

    user = relationship("User", back_populates="passkeys")

    def __repr__(self):
        return f"<Passkey(user_id={self.user_id}, name={self.name!r})>"
```

Then add the back-reference to the `User` class, next to the existing `overtime_shifts` and `employment_transition` relationships (around line 205):

```python
    passkeys = relationship("Passkey", back_populates="user", cascade="all, delete-orphan")
```

`Column`, `ForeignKey`, `Integer`, `String`, `Text`, `DateTime`, `relationship` and `utcnow` are all already imported in this file. Do not add imports.

- [ ] **Step 4: Run test to verify it passes**

Run: `venv/bin/python3 -m pytest tests/test_passkey_routes.py -v`
Expected: PASS

- [ ] **Step 5: Confirm the schema migrator picks up the new table**

Run: `venv/bin/python3 migrations/migrate_schema.py --dry-run`
Expected output contains: `Would create table passkeys`

This is the whole migration. Do not write a new migration script.

- [ ] **Step 6: Run the full suite to check nothing regressed**

Run: `venv/bin/python3 -m pytest -q`
Expected: PASS, same count as before plus one.

- [ ] **Step 7: Commit**

```bash
git add app/database/database.py tests/test_passkey_routes.py
git commit -m "feat(db): add passkeys table for WebAuthn credentials"
```

---

## Task 2: CBOR reader and COSE key parsing

**Files:**
- Create: `app/auth/webauthn.py`
- Create: `tests/test_webauthn.py`

**Interfaces:**
- Consumes: nothing
- Produces:
  - `class WebAuthnError(Exception)`
  - `cbor_decode(data: bytes) -> tuple[object, bytes]` returning `(value, remaining_bytes)`
  - `load_cose_key(cose_bytes: bytes)` returning a `cryptography` public key object, raising `WebAuthnError` for unsupported algorithms
  - `verify_signature(public_key, signed_bytes: bytes, signature: bytes) -> None`
  - `ALG_ES256 = -7`, `ALG_RS256 = -257`, `SUPPORTED_ALGORITHMS = (ALG_ES256, ALG_RS256)` (Task 5 imports `SUPPORTED_ALGORITHMS` to build `pubKeyCredParams`)

Tasks 3 and 4 append to this same file.

- [ ] **Step 1: Write the failing test**

Create `tests/test_webauthn.py`:

```python
# tests/test_webauthn.py
"""Tests for app/auth/webauthn.py, the hand-rolled WebAuthn verifier."""

import pytest
from cryptography.hazmat.primitives.asymmetric import ec, rsa

from app.auth.webauthn import WebAuthnError, cbor_decode, load_cose_key


def test_cbor_decodes_small_unsigned_int():
    assert cbor_decode(b"\x01") == (1, b"")


def test_cbor_decodes_multibyte_unsigned_int():
    # 0x19 = unsigned int, two-byte argument. 0x0100 = 256.
    assert cbor_decode(b"\x19\x01\x00") == (256, b"")


def test_cbor_decodes_negative_int():
    # 0x20 = negative int, argument 0, meaning -1. COSE uses -7 for ES256.
    assert cbor_decode(b"\x26") == (-7, b"")


def test_cbor_decodes_byte_string():
    assert cbor_decode(b"\x43abc") == (b"abc", b"")


def test_cbor_decodes_text_string():
    assert cbor_decode(b"\x63abc") == ("abc", b"")


def test_cbor_decodes_array():
    assert cbor_decode(b"\x82\x01\x02") == ([1, 2], b"")


def test_cbor_decodes_map():
    # {1: 2}
    assert cbor_decode(b"\xa1\x01\x02") == ({1: 2}, b"")


def test_cbor_returns_trailing_bytes():
    value, rest = cbor_decode(b"\x01\xff\xff")
    assert value == 1
    assert rest == b"\xff\xff"


def test_cbor_rejects_unsupported_major_type():
    # 0xc0 is major type 6, a tag, which this reader does not support.
    with pytest.raises(WebAuthnError):
        cbor_decode(b"\xc0\x01")


def test_cbor_rejects_truncated_input():
    with pytest.raises(WebAuthnError):
        cbor_decode(b"\x43ab")


def test_load_cose_key_reads_an_es256_key():
    private = ec.generate_private_key(ec.SECP256R1())
    numbers = private.public_key().public_numbers()
    cose = _es256_cose(numbers.x, numbers.y)

    loaded = load_cose_key(cose)

    assert isinstance(loaded, ec.EllipticCurvePublicKey)
    assert loaded.public_numbers().x == numbers.x


def test_load_cose_key_reads_an_rs256_key():
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    numbers = private.public_key().public_numbers()
    cose = _rs256_cose(numbers.n, numbers.e)

    loaded = load_cose_key(cose)

    assert isinstance(loaded, rsa.RSAPublicKey)
    assert loaded.public_numbers().n == numbers.n


def test_load_cose_key_rejects_an_unsupported_algorithm():
    private = ec.generate_private_key(ec.SECP256R1())
    numbers = private.public_key().public_numbers()
    # Same key, but labelled EdDSA (-8), which this module does not support.
    cose = _es256_cose(numbers.x, numbers.y, algorithm=-8)

    with pytest.raises(WebAuthnError):
        load_cose_key(cose)


# --- helpers that build COSE_Key structures the way an authenticator would ---


def _cbor_uint(major: int, value: int) -> bytes:
    """Encode a CBOR head for `major` with argument `value`."""
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


def _cbor_map(pairs: list[tuple[bytes, bytes]]) -> bytes:
    return _cbor_uint(5, len(pairs)) + b"".join(key + val for key, val in pairs)


def _es256_cose(x: int, y: int, algorithm: int = -7) -> bytes:
    """Build a COSE_Key for an EC2 P-256 public key."""
    return _cbor_map(
        [
            (_cbor_int(1), _cbor_int(2)),  # kty: EC2
            (_cbor_int(3), _cbor_int(algorithm)),  # alg
            (_cbor_int(-1), _cbor_int(1)),  # crv: P-256
            (_cbor_int(-2), _cbor_bytes(x.to_bytes(32, "big"))),
            (_cbor_int(-3), _cbor_bytes(y.to_bytes(32, "big"))),
        ]
    )


def _rs256_cose(n: int, e: int) -> bytes:
    """Build a COSE_Key for an RSA public key."""
    return _cbor_map(
        [
            (_cbor_int(1), _cbor_int(3)),  # kty: RSA
            (_cbor_int(3), _cbor_int(-257)),  # alg: RS256
            (_cbor_int(-1), _cbor_bytes(n.to_bytes((n.bit_length() + 7) // 8, "big"))),
            (_cbor_int(-2), _cbor_bytes(e.to_bytes((e.bit_length() + 7) // 8, "big"))),
        ]
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/bin/python3 -m pytest tests/test_webauthn.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.auth.webauthn'`

- [ ] **Step 3: Write the module**

Create `app/auth/webauthn.py`:

```python
# app/auth/webauthn.py
"""WebAuthn (passkey) verification, implemented against the standard library
and `cryptography`, which the app already depends on.

The alternative, py_webauthn, brings pyasn1, pyasn1-modules, cbor2 and pyOpenSSL
and requires a cryptography major-version bump, which is the dependency shape
this codebase deliberately shed when python-jose and passlib were removed. The
hand-rolled JWT in auth.py and the signed CSRF token in csrf.py set the pattern
followed here.

Registration requests `attestation: "none"`, so no attestation statement is ever
verified and no certificate chains need parsing. That is the recommended setting
for an application that does not restrict which authenticator models may enrol.
"""

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa


class WebAuthnError(Exception):
    """Any malformed, unsupported or failing WebAuthn input.

    One exception type for every rejection: callers turn it into a 400, and the
    distinction between "malformed CBOR" and "bad signature" is not something an
    unauthenticated client should be told.
    """


# --- CBOR ---
#
# A reader for the subset CTAP2 actually emits: unsigned ints, negative ints,
# byte strings, text strings, arrays and maps, with definite lengths. Tags,
# indefinite lengths, floats and simple values do not appear in an
# attestationObject or a COSE_Key, so they are rejected rather than guessed at.

_MAJOR_UNSIGNED = 0
_MAJOR_NEGATIVE = 1
_MAJOR_BYTES = 2
_MAJOR_TEXT = 3
_MAJOR_ARRAY = 4
_MAJOR_MAP = 5

_ARGUMENT_WIDTHS = {24: 1, 25: 2, 26: 4, 27: 8}


def _read_head(data: bytes) -> tuple[int, int, bytes]:
    """Split off one CBOR head, returning (major type, argument, remainder)."""
    if not data:
        raise WebAuthnError("Truncated CBOR input")
    major = data[0] >> 5
    minor = data[0] & 0x1F
    rest = data[1:]

    if minor < 24:
        return major, minor, rest
    width = _ARGUMENT_WIDTHS.get(minor)
    if width is None:
        # 28-30 are reserved, 31 is an indefinite length.
        raise WebAuthnError(f"Unsupported CBOR argument {minor}")
    if len(rest) < width:
        raise WebAuthnError("Truncated CBOR argument")
    return major, int.from_bytes(rest[:width], "big"), rest[width:]


def cbor_decode(data: bytes) -> tuple[object, bytes]:
    """Decode one CBOR value, returning it with whatever bytes follow it."""
    major, argument, rest = _read_head(data)

    if major == _MAJOR_UNSIGNED:
        return argument, rest
    if major == _MAJOR_NEGATIVE:
        return -argument - 1, rest
    if major in (_MAJOR_BYTES, _MAJOR_TEXT):
        if len(rest) < argument:
            raise WebAuthnError("Truncated CBOR string")
        raw, rest = rest[:argument], rest[argument:]
        return (raw.decode("utf-8") if major == _MAJOR_TEXT else raw), rest
    if major == _MAJOR_ARRAY:
        items = []
        for _ in range(argument):
            item, rest = cbor_decode(rest)
            items.append(item)
        return items, rest
    if major == _MAJOR_MAP:
        mapping: dict = {}
        for _ in range(argument):
            key, rest = cbor_decode(rest)
            value, rest = cbor_decode(rest)
            mapping[key] = value
        return mapping, rest

    raise WebAuthnError(f"Unsupported CBOR major type {major}")


# --- COSE keys ---
#
# COSE_Key label numbers from RFC 8152. Only the two algorithms browsers and
# platform authenticators actually negotiate are accepted.

_COSE_KTY = 1
_COSE_ALG = 3
_COSE_EC2_CRV = -1
_COSE_EC2_X = -2
_COSE_EC2_Y = -3
_COSE_RSA_N = -1
_COSE_RSA_E = -2

_KTY_EC2 = 2
_KTY_RSA = 3

ALG_ES256 = -7
ALG_RS256 = -257
SUPPORTED_ALGORITHMS = (ALG_ES256, ALG_RS256)

_CRV_P256 = 1


def load_cose_key(cose_bytes: bytes):
    """Turn raw COSE_Key bytes into a cryptography public key object."""
    key, _ = cbor_decode(cose_bytes)
    if not isinstance(key, dict):
        raise WebAuthnError("COSE key is not a map")

    algorithm = key.get(_COSE_ALG)
    if algorithm not in SUPPORTED_ALGORITHMS:
        raise WebAuthnError(f"Unsupported COSE algorithm {algorithm!r}")

    kty = key.get(_COSE_KTY)
    if algorithm == ALG_ES256:
        if kty != _KTY_EC2 or key.get(_COSE_EC2_CRV) != _CRV_P256:
            raise WebAuthnError("ES256 key is not an EC2 P-256 key")
        x = key.get(_COSE_EC2_X)
        y = key.get(_COSE_EC2_Y)
        if not isinstance(x, bytes) or not isinstance(y, bytes):
            raise WebAuthnError("ES256 key is missing its coordinates")
        return ec.EllipticCurvePublicNumbers(
            int.from_bytes(x, "big"), int.from_bytes(y, "big"), ec.SECP256R1()
        ).public_key()

    if kty != _KTY_RSA:
        raise WebAuthnError("RS256 key is not an RSA key")
    n = key.get(_COSE_RSA_N)
    e = key.get(_COSE_RSA_E)
    if not isinstance(n, bytes) or not isinstance(e, bytes):
        raise WebAuthnError("RS256 key is missing its modulus or exponent")
    return rsa.RSAPublicNumbers(int.from_bytes(e, "big"), int.from_bytes(n, "big")).public_key()


def verify_signature(public_key, signed_bytes: bytes, signature: bytes) -> None:
    """Verify `signature` over `signed_bytes`, raising WebAuthnError on failure."""
    try:
        if isinstance(public_key, ec.EllipticCurvePublicKey):
            public_key.verify(signature, signed_bytes, ec.ECDSA(hashes.SHA256()))
        else:
            public_key.verify(signature, signed_bytes, padding.PKCS1v15(), hashes.SHA256())
    except InvalidSignature as error:
        raise WebAuthnError("Signature verification failed") from error
```

`verify_signature` is written now because it belongs with the key loading, but it is not tested until Task 4 where a full signed assertion exists to test it against.

- [ ] **Step 4: Run test to verify it passes**

Run: `venv/bin/python3 -m pytest tests/test_webauthn.py -v`
Expected: PASS, 13 tests.

- [ ] **Step 5: Lint**

Run: `venv/bin/python3 -m ruff check app/auth/webauthn.py tests/test_webauthn.py && venv/bin/python3 -m ruff format --check app/auth/webauthn.py tests/test_webauthn.py`
Expected: no findings. If `ruff format --check` reports the files would be reformatted, run `venv/bin/python3 -m ruff format` on them and re-run the check.

- [ ] **Step 6: Commit**

```bash
git add app/auth/webauthn.py tests/test_webauthn.py
git commit -m "feat(auth): add CBOR reader and COSE key parsing for WebAuthn"
```

---

## Task 3: Signed challenges and client data validation

**Files:**
- Modify: `app/auth/webauthn.py` (append)
- Modify: `tests/test_webauthn.py` (append)

**Interfaces:**
- Consumes: `WebAuthnError` from Task 2
- Produces:
  - `CHALLENGE_COOKIE_NAME: str` (value `"webauthn_challenge"`)
  - `CHALLENGE_TTL_SECONDS: int` (value `300`)
  - `new_challenge() -> tuple[str, str]` returning `(challenge, cookie_value)`
  - `challenge_from_cookie(cookie_value: str | None) -> str` returning the nonce, raising `WebAuthnError`
  - `verify_client_data(client_data_json: bytes, expected_type: str, expected_challenge: str, rp_id: str, origin: str | None) -> None`
  - `b64url_decode(value: str) -> bytes` and `b64url_encode(raw: bytes) -> str`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_webauthn.py`:

```python
def test_new_challenge_round_trips_through_its_cookie():
    challenge, cookie = webauthn.new_challenge()
    assert webauthn.challenge_from_cookie(cookie) == challenge


def test_challenge_cookie_with_a_tampered_nonce_is_rejected():
    _, cookie = webauthn.new_challenge()
    _, expiry, signature = cookie.split(".")
    with pytest.raises(WebAuthnError):
        webauthn.challenge_from_cookie(f"tampered.{expiry}.{signature}")


def test_expired_challenge_cookie_is_rejected():
    challenge, cookie = webauthn.new_challenge()
    nonce, _, _ = cookie.split(".")
    past = int(time.time()) - 1
    forged = f"{nonce}.{past}.{webauthn._sign_challenge(nonce, past)}"
    assert nonce == challenge
    with pytest.raises(WebAuthnError):
        webauthn.challenge_from_cookie(forged)


def test_missing_challenge_cookie_is_rejected():
    with pytest.raises(WebAuthnError):
        webauthn.challenge_from_cookie(None)


def test_valid_client_data_passes():
    challenge, _ = webauthn.new_challenge()
    client_data = _client_data("webauthn.get", challenge, "https://example.test")

    webauthn.verify_client_data(
        client_data, "webauthn.get", challenge, "example.test", "https://example.test"
    )


def test_client_data_with_the_wrong_type_is_rejected():
    challenge, _ = webauthn.new_challenge()
    client_data = _client_data("webauthn.create", challenge, "https://example.test")

    with pytest.raises(WebAuthnError):
        webauthn.verify_client_data(
            client_data, "webauthn.get", challenge, "example.test", "https://example.test"
        )


def test_client_data_with_the_wrong_challenge_is_rejected():
    challenge, _ = webauthn.new_challenge()
    other, _ = webauthn.new_challenge()
    client_data = _client_data("webauthn.get", other, "https://example.test")

    with pytest.raises(WebAuthnError):
        webauthn.verify_client_data(
            client_data, "webauthn.get", challenge, "example.test", "https://example.test"
        )


def test_client_data_from_another_origin_is_rejected():
    challenge, _ = webauthn.new_challenge()
    client_data = _client_data("webauthn.get", challenge, "https://evil.test")

    with pytest.raises(WebAuthnError):
        webauthn.verify_client_data(
            client_data, "webauthn.get", challenge, "example.test", "https://example.test"
        )


def test_plain_http_origin_is_rejected_off_localhost():
    challenge, _ = webauthn.new_challenge()
    client_data = _client_data("webauthn.get", challenge, "http://example.test")

    with pytest.raises(WebAuthnError):
        webauthn.verify_client_data(
            client_data, "webauthn.get", challenge, "example.test", "http://example.test"
        )


def test_plain_http_localhost_origin_is_allowed():
    challenge, _ = webauthn.new_challenge()
    client_data = _client_data("webauthn.get", challenge, "http://localhost:8000")

    webauthn.verify_client_data(
        client_data, "webauthn.get", challenge, "localhost", "http://localhost:8000"
    )


def test_origin_header_that_disagrees_with_client_data_is_rejected():
    """The browser sends both; they must agree, or something is proxying."""
    challenge, _ = webauthn.new_challenge()
    client_data = _client_data("webauthn.get", challenge, "https://example.test")

    with pytest.raises(WebAuthnError):
        webauthn.verify_client_data(
            client_data, "webauthn.get", challenge, "example.test", "https://other.test"
        )


def _client_data(data_type: str, challenge: str, origin: str) -> bytes:
    """Build a clientDataJSON blob the way a browser would."""
    return json.dumps(
        {"type": data_type, "challenge": challenge, "origin": origin, "crossOrigin": False}
    ).encode("utf-8")
```

Extend the import block at the top of `tests/test_webauthn.py` to:

```python
import json
import time

import pytest
from cryptography.hazmat.primitives.asymmetric import ec, rsa

from app.auth import webauthn
from app.auth.webauthn import WebAuthnError, cbor_decode, load_cose_key
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/bin/python3 -m pytest tests/test_webauthn.py -v`
Expected: the Task 2 tests still PASS, the new ones FAIL with `AttributeError: module 'app.auth.webauthn' has no attribute 'new_challenge'`

- [ ] **Step 3: Append the implementation**

Add to the imports at the top of `app/auth/webauthn.py`:

```python
import base64
import hashlib
import hmac
import json
import secrets
import time
from urllib.parse import urlparse

from app.auth.auth import SECRET_KEY
```

Append to `app/auth/webauthn.py`:

```python
# --- base64url ---
#
# WebAuthn sends every binary field base64url encoded, and browsers strip the
# padding. Both directions are needed, so both live here rather than being
# re-derived at each call site.


def b64url_encode(raw: bytes) -> str:
    """Base64url without padding, matching what browsers send."""
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def b64url_decode(value: str) -> bytes:
    """Reverse of b64url_encode, restoring the padding the browser stripped."""
    try:
        return base64.urlsafe_b64decode(value.encode("ascii") + b"=" * (-len(value) % 4))
    except (ValueError, UnicodeError) as error:
        raise WebAuthnError("Malformed base64url value") from error


# --- Challenges ---
#
# The server keeps no copy of the challenge it issued. It hands the browser a
# random nonce and puts "<nonce>.<expiry>.<signature>" in a short-lived cookie,
# signed with SECRET_KEY the way csrf.py signs its token. Verification re-signs
# what the cookie claims and compares. No table, no cleanup job, no session
# store, and a challenge cannot outlive its expiry even if the cookie does.

CHALLENGE_COOKIE_NAME = "webauthn_challenge"
CHALLENGE_TTL_SECONDS = 300

_CHALLENGE_BYTES = 32


def _sign_challenge(nonce: str, expiry: int) -> str:
    """Return the HMAC-SHA256 signature binding a nonce and expiry to this server."""
    message = f"{nonce}.{expiry}".encode("utf-8")
    return hmac.new(SECRET_KEY.encode("utf-8"), message, hashlib.sha256).hexdigest()


def new_challenge() -> tuple[str, str]:
    """Return (challenge for the browser, value for the challenge cookie)."""
    nonce = b64url_encode(secrets.token_bytes(_CHALLENGE_BYTES))
    expiry = int(time.time()) + CHALLENGE_TTL_SECONDS
    return nonce, f"{nonce}.{expiry}.{_sign_challenge(nonce, expiry)}"


def challenge_from_cookie(cookie_value: str | None) -> str:
    """Return the challenge a cookie vouches for, or raise WebAuthnError."""
    if not cookie_value:
        raise WebAuthnError("Missing challenge cookie")

    parts = cookie_value.split(".")
    if len(parts) != 3:
        raise WebAuthnError("Malformed challenge cookie")
    nonce, expiry_text, signature = parts

    try:
        expiry = int(expiry_text)
    except ValueError as error:
        raise WebAuthnError("Malformed challenge expiry") from error

    if not hmac.compare_digest(signature, _sign_challenge(nonce, expiry)):
        raise WebAuthnError("Challenge cookie signature does not verify")
    if time.time() >= expiry:
        raise WebAuthnError("Challenge has expired")

    return nonce


# --- Client data ---


def _origin_is_secure(parsed) -> bool:
    """True when an origin is one WebAuthn will operate on.

    https always, plus http on localhost, which browsers treat as a secure
    context so the app can be exercised in development.
    """
    if parsed.scheme == "https":
        return True
    return parsed.scheme == "http" and parsed.hostname in ("localhost", "127.0.0.1")


def verify_client_data(
    client_data_json: bytes,
    expected_type: str,
    expected_challenge: str,
    rp_id: str,
    origin: str | None,
) -> None:
    """Check the collected client data against what this server asked for.

    The origin is validated against what the browser signed rather than
    reconstructed from the request URL: a reverse proxy rewrites the scheme the
    application sees, while clientDataJSON carries the origin the browser
    actually used. When the request also carried an Origin header, the two must
    agree.
    """
    try:
        client_data = json.loads(client_data_json)
    except (ValueError, UnicodeError) as error:
        raise WebAuthnError("Malformed clientDataJSON") from error
    if not isinstance(client_data, dict):
        raise WebAuthnError("clientDataJSON is not an object")

    if client_data.get("type") != expected_type:
        raise WebAuthnError("Unexpected clientData type")

    challenge = client_data.get("challenge")
    if not isinstance(challenge, str) or not hmac.compare_digest(challenge, expected_challenge):
        raise WebAuthnError("Challenge does not match")

    client_origin = client_data.get("origin")
    if not isinstance(client_origin, str):
        raise WebAuthnError("clientData carries no origin")
    parsed = urlparse(client_origin)
    if parsed.hostname != rp_id:
        raise WebAuthnError("Origin does not belong to this relying party")
    if not _origin_is_secure(parsed):
        raise WebAuthnError("Origin is not a secure context")
    if origin is not None and origin != client_origin:
        raise WebAuthnError("Origin header disagrees with clientData")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `venv/bin/python3 -m pytest tests/test_webauthn.py -v`
Expected: PASS, 24 tests.

- [ ] **Step 5: Lint**

Run: `venv/bin/python3 -m ruff check app/auth/webauthn.py tests/test_webauthn.py && venv/bin/python3 -m ruff format --check app/auth/webauthn.py tests/test_webauthn.py`
Expected: no findings.

- [ ] **Step 6: Commit**

```bash
git add app/auth/webauthn.py tests/test_webauthn.py
git commit -m "feat(auth): add stateless signed WebAuthn challenges and client data checks"
```

---

## Task 4: Registration and assertion verification

**Files:**
- Modify: `app/auth/webauthn.py` (append)
- Modify: `tests/test_webauthn.py` (append)

**Interfaces:**
- Consumes: everything from Tasks 2 and 3
- Produces:
  - `@dataclass class RegisteredCredential` with fields `credential_id: str`, `public_key: str`, `sign_count: int`
  - `verify_registration(client_data_json: bytes, attestation_object: bytes, expected_challenge: str, rp_id: str, origin: str | None) -> RegisteredCredential`
  - `verify_assertion(client_data_json: bytes, authenticator_data: bytes, signature: bytes, stored_public_key: str, stored_sign_count: int, expected_challenge: str, rp_id: str, origin: str | None) -> int` returning the new sign count

- [ ] **Step 1: Write the failing test**

Append to `tests/test_webauthn.py`:

```python
# --- registration and assertion ---

RP_ID = "example.test"
ORIGIN = "https://example.test"

FLAG_UP = 0x01
FLAG_UV = 0x04
FLAG_AT = 0x40


def _auth_data(flags: int, sign_count: int, attested: bytes = b"") -> bytes:
    """Build authenticatorData for RP_ID."""
    return hashlib.sha256(RP_ID.encode()).digest() + bytes([flags]) + sign_count.to_bytes(4, "big") + attested


def _attested_credential_data(credential_id: bytes, cose_key: bytes) -> bytes:
    """Build the attested credential data block appended during registration."""
    return b"\x00" * 16 + len(credential_id).to_bytes(2, "big") + credential_id + cose_key


def _attestation_object(auth_data: bytes) -> bytes:
    """Wrap authData in an attestationObject with fmt "none"."""
    return _cbor_map(
        [
            (_cbor_text("fmt"), _cbor_text("none")),
            (_cbor_text("attStmt"), _cbor_map([])),
            (_cbor_text("authData"), _cbor_bytes(auth_data)),
        ]
    )


def _cbor_text(value: str) -> bytes:
    raw = value.encode("utf-8")
    return _cbor_uint(3, len(raw)) + raw


def _make_registration(flags: int = FLAG_UP | FLAG_UV | FLAG_AT, sign_count: int = 0):
    """Return (private key, credential_id bytes, client_data, attestation_object, challenge)."""
    private = ec.generate_private_key(ec.SECP256R1())
    numbers = private.public_key().public_numbers()
    cose = _es256_cose(numbers.x, numbers.y)
    credential_id = b"credential-id-0001"

    challenge, _ = webauthn.new_challenge()
    client_data = _client_data("webauthn.create", challenge, ORIGIN)
    auth_data = _auth_data(flags, sign_count, _attested_credential_data(credential_id, cose))
    return private, credential_id, client_data, _attestation_object(auth_data), challenge


def _sign_assertion(private, auth_data: bytes, client_data: bytes) -> bytes:
    """Sign authData || sha256(clientDataJSON) the way an authenticator does."""
    return private.sign(
        auth_data + hashlib.sha256(client_data).digest(), ec.ECDSA(hashes.SHA256())
    )


def test_registration_returns_the_credential():
    _, credential_id, client_data, attestation, challenge = _make_registration()

    result = webauthn.verify_registration(client_data, attestation, challenge, RP_ID, ORIGIN)

    assert result.credential_id == webauthn.b64url_encode(credential_id)
    assert result.sign_count == 0
    # The stored key round-trips back through the COSE loader.
    assert load_cose_key(webauthn.b64url_decode(result.public_key)) is not None


def test_registration_without_user_verification_is_rejected():
    _, _, client_data, attestation, challenge = _make_registration(flags=FLAG_UP | FLAG_AT)

    with pytest.raises(WebAuthnError):
        webauthn.verify_registration(client_data, attestation, challenge, RP_ID, ORIGIN)


def test_registration_without_user_presence_is_rejected():
    _, _, client_data, attestation, challenge = _make_registration(flags=FLAG_UV | FLAG_AT)

    with pytest.raises(WebAuthnError):
        webauthn.verify_registration(client_data, attestation, challenge, RP_ID, ORIGIN)


def test_registration_for_another_relying_party_is_rejected():
    _, _, client_data, attestation, challenge = _make_registration()

    with pytest.raises(WebAuthnError):
        webauthn.verify_registration(client_data, attestation, challenge, "other.test", ORIGIN)


def test_assertion_verifies_and_returns_the_new_sign_count():
    private, credential_id, _, attestation, reg_challenge = _make_registration()
    reg_client_data = _client_data("webauthn.create", reg_challenge, ORIGIN)
    registered = webauthn.verify_registration(reg_client_data, attestation, reg_challenge, RP_ID, ORIGIN)

    challenge, _ = webauthn.new_challenge()
    client_data = _client_data("webauthn.get", challenge, ORIGIN)
    auth_data = _auth_data(FLAG_UP | FLAG_UV, 5)
    signature = _sign_assertion(private, auth_data, client_data)

    new_count = webauthn.verify_assertion(
        client_data, auth_data, signature, registered.public_key, 0, challenge, RP_ID, ORIGIN
    )

    assert new_count == 5


def test_assertion_with_a_forged_signature_is_rejected():
    private, _, _, attestation, reg_challenge = _make_registration()
    reg_client_data = _client_data("webauthn.create", reg_challenge, ORIGIN)
    registered = webauthn.verify_registration(reg_client_data, attestation, reg_challenge, RP_ID, ORIGIN)

    challenge, _ = webauthn.new_challenge()
    client_data = _client_data("webauthn.get", challenge, ORIGIN)
    auth_data = _auth_data(FLAG_UP | FLAG_UV, 5)
    # Signed by a different key entirely.
    other = ec.generate_private_key(ec.SECP256R1())
    signature = _sign_assertion(other, auth_data, client_data)

    with pytest.raises(WebAuthnError):
        webauthn.verify_assertion(
            client_data, auth_data, signature, registered.public_key, 0, challenge, RP_ID, ORIGIN
        )


def test_replayed_sign_count_is_rejected():
    private, _, _, attestation, reg_challenge = _make_registration()
    reg_client_data = _client_data("webauthn.create", reg_challenge, ORIGIN)
    registered = webauthn.verify_registration(reg_client_data, attestation, reg_challenge, RP_ID, ORIGIN)

    challenge, _ = webauthn.new_challenge()
    client_data = _client_data("webauthn.get", challenge, ORIGIN)
    auth_data = _auth_data(FLAG_UP | FLAG_UV, 5)
    signature = _sign_assertion(private, auth_data, client_data)

    with pytest.raises(WebAuthnError):
        webauthn.verify_assertion(
            client_data, auth_data, signature, registered.public_key, 5, challenge, RP_ID, ORIGIN
        )


def test_sign_count_of_zero_is_accepted_repeatedly():
    """Authenticators without a counter always send 0, so 0 cannot be a replay."""
    private, _, _, attestation, reg_challenge = _make_registration()
    reg_client_data = _client_data("webauthn.create", reg_challenge, ORIGIN)
    registered = webauthn.verify_registration(reg_client_data, attestation, reg_challenge, RP_ID, ORIGIN)

    challenge, _ = webauthn.new_challenge()
    client_data = _client_data("webauthn.get", challenge, ORIGIN)
    auth_data = _auth_data(FLAG_UP | FLAG_UV, 0)
    signature = _sign_assertion(private, auth_data, client_data)

    assert (
        webauthn.verify_assertion(
            client_data, auth_data, signature, registered.public_key, 0, challenge, RP_ID, ORIGIN
        )
        == 0
    )


def test_assertion_without_user_verification_is_rejected():
    private, _, _, attestation, reg_challenge = _make_registration()
    reg_client_data = _client_data("webauthn.create", reg_challenge, ORIGIN)
    registered = webauthn.verify_registration(reg_client_data, attestation, reg_challenge, RP_ID, ORIGIN)

    challenge, _ = webauthn.new_challenge()
    client_data = _client_data("webauthn.get", challenge, ORIGIN)
    auth_data = _auth_data(FLAG_UP, 5)
    signature = _sign_assertion(private, auth_data, client_data)

    with pytest.raises(WebAuthnError):
        webauthn.verify_assertion(
            client_data, auth_data, signature, registered.public_key, 0, challenge, RP_ID, ORIGIN
        )
```

Extend the import block at the top of `tests/test_webauthn.py` to add `hashlib` and the `hashes` primitive:

```python
import hashlib
import json
import time

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, rsa

from app.auth import webauthn
from app.auth.webauthn import WebAuthnError, cbor_decode, load_cose_key
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/bin/python3 -m pytest tests/test_webauthn.py -v`
Expected: the earlier tests PASS, the new ones FAIL with `AttributeError: module 'app.auth.webauthn' has no attribute 'verify_registration'`

- [ ] **Step 3: Append the implementation**

Add `from dataclasses import dataclass` to the imports at the top of `app/auth/webauthn.py`.

Append to `app/auth/webauthn.py`:

```python
# --- Authenticator data ---
#
# Fixed binary layout from the WebAuthn spec: 32-byte RP ID hash, one flags
# byte, a big-endian 4-byte signature counter, then, when the AT flag is set,
# the attested credential data (16-byte AAGUID, 2-byte credential ID length,
# the credential ID, and the COSE public key filling the rest).

_RP_ID_HASH_LENGTH = 32
_FLAGS_OFFSET = 32
_SIGN_COUNT_OFFSET = 33
_ATTESTED_DATA_OFFSET = 37
_AAGUID_LENGTH = 16

FLAG_USER_PRESENT = 0x01
FLAG_USER_VERIFIED = 0x04
FLAG_ATTESTED_CREDENTIAL_DATA = 0x40


@dataclass
class RegisteredCredential:
    """What a verified registration yields, ready to store on a Passkey row."""

    credential_id: str
    public_key: str
    sign_count: int


def _check_authenticator_data(authenticator_data: bytes, rp_id: str) -> tuple[int, int]:
    """Validate the fixed part of authenticatorData, returning (flags, sign count).

    User verification is required rather than merely preferred: a passkey here
    stands in for a password, so an authenticator that only proves someone
    touched it is not enough.
    """
    if len(authenticator_data) < _ATTESTED_DATA_OFFSET:
        raise WebAuthnError("Truncated authenticator data")

    if not hmac.compare_digest(
        authenticator_data[:_RP_ID_HASH_LENGTH], hashlib.sha256(rp_id.encode("utf-8")).digest()
    ):
        raise WebAuthnError("Authenticator data is for a different relying party")

    flags = authenticator_data[_FLAGS_OFFSET]
    if not flags & FLAG_USER_PRESENT:
        raise WebAuthnError("User presence flag is not set")
    if not flags & FLAG_USER_VERIFIED:
        raise WebAuthnError("User verification flag is not set")

    sign_count = int.from_bytes(
        authenticator_data[_SIGN_COUNT_OFFSET:_ATTESTED_DATA_OFFSET], "big"
    )
    return flags, sign_count


def verify_registration(
    client_data_json: bytes,
    attestation_object: bytes,
    expected_challenge: str,
    rp_id: str,
    origin: str | None,
) -> RegisteredCredential:
    """Verify a navigator.credentials.create() response and return what to store."""
    verify_client_data(client_data_json, "webauthn.create", expected_challenge, rp_id, origin)

    attestation, rest = cbor_decode(attestation_object)
    if rest:
        raise WebAuthnError("Trailing bytes after attestationObject")
    if not isinstance(attestation, dict):
        raise WebAuthnError("attestationObject is not a map")

    authenticator_data = attestation.get("authData")
    if not isinstance(authenticator_data, bytes):
        raise WebAuthnError("attestationObject carries no authData")

    flags, sign_count = _check_authenticator_data(authenticator_data, rp_id)
    if not flags & FLAG_ATTESTED_CREDENTIAL_DATA:
        raise WebAuthnError("Registration carries no attested credential data")

    attested = authenticator_data[_ATTESTED_DATA_OFFSET:]
    if len(attested) < _AAGUID_LENGTH + 2:
        raise WebAuthnError("Truncated attested credential data")

    id_length = int.from_bytes(attested[_AAGUID_LENGTH : _AAGUID_LENGTH + 2], "big")
    id_start = _AAGUID_LENGTH + 2
    credential_id = attested[id_start : id_start + id_length]
    if len(credential_id) != id_length:
        raise WebAuthnError("Truncated credential ID")

    cose_bytes = attested[id_start + id_length :]
    # Parsed and discarded: this proves the stored bytes are a key this server can
    # actually verify with later, rather than finding out at the first login.
    load_cose_key(cose_bytes)

    return RegisteredCredential(
        credential_id=b64url_encode(credential_id),
        public_key=b64url_encode(cose_bytes),
        sign_count=sign_count,
    )


def verify_assertion(
    client_data_json: bytes,
    authenticator_data: bytes,
    signature: bytes,
    stored_public_key: str,
    stored_sign_count: int,
    expected_challenge: str,
    rp_id: str,
    origin: str | None,
) -> int:
    """Verify a navigator.credentials.get() response, returning the new sign count."""
    verify_client_data(client_data_json, "webauthn.get", expected_challenge, rp_id, origin)
    _, sign_count = _check_authenticator_data(authenticator_data, rp_id)

    # A counter that did not advance means the assertion was replayed, or the
    # credential was cloned. Authenticators that implement no counter send 0
    # every time, and 0 against a stored 0 is the normal case for them.
    if sign_count != 0 or stored_sign_count != 0:
        if sign_count <= stored_sign_count:
            raise WebAuthnError("Signature counter did not advance")

    public_key = load_cose_key(b64url_decode(stored_public_key))
    verify_signature(
        public_key, authenticator_data + hashlib.sha256(client_data_json).digest(), signature
    )
    return sign_count
```

- [ ] **Step 4: Run test to verify it passes**

Run: `venv/bin/python3 -m pytest tests/test_webauthn.py -v`
Expected: PASS, 33 tests.

- [ ] **Step 5: Lint**

Run: `venv/bin/python3 -m ruff check app/auth/webauthn.py tests/test_webauthn.py && venv/bin/python3 -m ruff format --check app/auth/webauthn.py tests/test_webauthn.py`
Expected: no findings.

- [ ] **Step 6: Commit**

```bash
git add app/auth/webauthn.py tests/test_webauthn.py
git commit -m "feat(auth): verify WebAuthn registration and assertion responses"
```

---

## Task 5: The routes

**Files:**
- Create: `app/routes/passkey_routes.py`
- Modify: `app/main.py` (import near the other route imports, `include_router` near line 348)
- Modify: `tests/test_passkey_routes.py` (append)

**Interfaces:**
- Consumes: `Passkey` (Task 1), everything in `app.auth.webauthn` (Tasks 2-4)
- Produces: `router` exported from `app.routes.passkey_routes`, serving `/passkey/register/options`, `/passkey/register`, `/passkey/login/options`, `/passkey/login`, `/profile/passkey/{passkey_id}/delete`

**Design notes for the implementer:**

`CSRFMiddleware` reads the CSRF token only out of urlencoded and multipart bodies and fails closed on anything else, so these endpoints take urlencoded form bodies, not JSON, and read them with `Form(...)` like the rest of the app. The credential itself travels as a JSON string in a `credential` field.

The RP ID comes from `request.url.hostname`. The expected origin comes from the request's `Origin` header, passed through to the verifier which checks it against what the browser signed.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_passkey_routes.py`:

```python
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

    response = test_client.post(
        f"/profile/passkey/{passkey_id}/delete", follow_redirects=False
    )

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
    passkey = Passkey(
        user_id=other.id, credential_id="b3RoZXI", public_key="a2V5", sign_count=0, name="Other"
    )
    test_db.add(passkey)
    test_db.commit()
    _login(test_client, test_user)

    response = test_client.post(
        f"/profile/passkey/{passkey.id}/delete", follow_redirects=False
    )

    assert response.status_code == 404
    assert test_db.query(Passkey).count() == 1
```

Add this helper block and the imports at the top of `tests/test_passkey_routes.py`:

```python
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
# tests therefore run with PRODUCTION unset and patch the secure-context rule; see
# the autouse fixture below.
TEST_RP_ID = "testserver"
TEST_ORIGIN = "http://testserver"

FLAG_UP = 0x01
FLAG_UV = 0x04


@pytest.fixture(autouse=True)
def allow_testserver_origin(monkeypatch):
    """Treat http://testserver as a secure context for the duration of a test.

    WebAuthn only runs on https or localhost, and TestClient's host is neither.
    Patching the one predicate that encodes that rule keeps the rule itself under
    test in tests/test_webauthn.py, where a real hostname can be used.
    """
    monkeypatch.setattr(
        webauthn, "_origin_is_secure", lambda parsed: parsed.hostname in ("testserver", "localhost")
    )


def _login(client, user):
    """Give the client an authenticated session cookie for `user`."""
    client.cookies.set("access_token", f"Bearer {create_access_token(data={'sub': str(user.id)})}")


def _client_data(data_type: str, challenge: str, origin: str) -> bytes:
    return json.dumps({"type": data_type, "challenge": challenge, "origin": origin}).encode("utf-8")


def _auth_data(flags: int, sign_count: int) -> bytes:
    return (
        hashlib.sha256(TEST_RP_ID.encode()).digest()
        + bytes([flags])
        + sign_count.to_bytes(4, "big")
    )


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


def _cbor_text(value: str) -> bytes:
    raw = value.encode("utf-8")
    return _cbor_uint(3, len(raw)) + raw


def _attestation_object(private, credential_id: bytes) -> bytes:
    """Build an attestationObject with fmt "none" for a freshly generated key."""
    numbers = private.public_key().public_numbers()
    cose = _es256_cose(numbers.x, numbers.y)
    attested = (
        b"\x00" * 16 + len(credential_id).to_bytes(2, "big") + credential_id + cose
    )
    # AT flag (0x40) marks the attested credential data as present.
    auth_data = _auth_data(FLAG_UP | FLAG_UV | 0x40, 0) + attested
    return _cbor_uint(5, 3) + b"".join(
        [
            _cbor_text("fmt") + _cbor_text("none"),
            _cbor_text("attStmt") + _cbor_uint(5, 0),
            _cbor_text("authData") + _cbor_bytes(auth_data),
        ]
    )
```

These fixture names are the real ones in `tests/conftest.py`, already verified: `test_client`, `test_db`, `test_user`. The cookie-setting `_login` helper matches how `tests/test_base_layout.py` and `tests/test_news_indicator.py` already authenticate.

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/bin/python3 -m pytest tests/test_passkey_routes.py -v`
Expected: the Task 1 test PASSes, the new ones FAIL with 404s (the routes do not exist).

- [ ] **Step 3: Write the routes**

Create `app/routes/passkey_routes.py`:

```python
# app/routes/passkey_routes.py
"""Passkey (WebAuthn) registration and login routes.

Login here ends in exactly the same place password login does: create_access_token,
set_auth_cookie, log_auth_event, and the pending-password-change redirect. There is
one session-issuing sequence in this app, and adding a second way in must not
become a second way to get one wrong.

Bodies are urlencoded rather than JSON because CSRFMiddleware reads its token out
of form bodies and fails closed on any other content type. The credential travels
as a JSON string in the `credential` field.
"""

import json
import os

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.auth.auth import create_access_token, get_current_user, set_auth_cookie
from app.auth.webauthn import (
    CHALLENGE_COOKIE_NAME,
    CHALLENGE_TTL_SECONDS,
    SUPPORTED_ALGORITHMS,
    WebAuthnError,
    b64url_decode,
    challenge_from_cookie,
    new_challenge,
    verify_assertion,
    verify_registration,
)
from app.core.logging_config import get_logger
from app.core.request_logging import log_auth_event
from app.core.sentry_config import add_breadcrumb, set_user_context
from app.database.database import Passkey, User, get_db, utcnow

logger = get_logger(__name__)

router = APIRouter(tags=["passkey"])

RP_NAME = "Periodical"

# Default label for a newly registered passkey when the browser offers nothing
# better. The user can rename it later from the profile page.
DEFAULT_PASSKEY_NAME = "Passkey"


def _rp_id(request: Request) -> str:
    """The relying party ID: the hostname the browser used, nothing configured.

    A passkey is bound to this value, so a credential registered against the dev
    hostname will not work against the prod hostname. That is the intended
    behaviour and needs no setting.
    """
    return request.url.hostname or "localhost"


def _origin(request: Request) -> str | None:
    return request.headers.get("origin")


def _secure_cookies() -> bool:
    """Whether to mark cookies Secure, read the same way set_auth_cookie reads it."""
    return os.getenv("PRODUCTION", "false").lower() == "true"


def _challenge_response(payload: dict, cookie_value: str) -> JSONResponse:
    """Return options JSON with the signed challenge attached as a cookie."""
    response = JSONResponse(payload)
    response.set_cookie(
        key=CHALLENGE_COOKIE_NAME,
        value=cookie_value,
        httponly=True,
        max_age=CHALLENGE_TTL_SECONDS,
        samesite="lax",
        secure=_secure_cookies(),
    )
    return response


@router.post("/passkey/register/options", name="passkey_register_options")
async def passkey_register_options(
    request: Request,
    current_user: User = Depends(get_current_user),
):
    """Return creation options for enrolling a new passkey."""
    challenge, cookie_value = new_challenge()
    return _challenge_response(
        {
            "challenge": challenge,
            "rp": {"id": _rp_id(request), "name": RP_NAME},
            "user": {
                "id": str(current_user.id),
                "name": current_user.username,
                "displayName": current_user.name,
            },
            "pubKeyCredParams": [
                {"type": "public-key", "alg": algorithm} for algorithm in SUPPORTED_ALGORITHMS
            ],
            "authenticatorSelection": {
                # A discoverable credential is what makes the login button work
                # without a username field.
                "residentKey": "required",
                "requireResidentKey": True,
                "userVerification": "required",
            },
            # No attestation is requested, so none is verified.
            "attestation": "none",
            "timeout": CHALLENGE_TTL_SECONDS * 1000,
        },
        cookie_value,
    )


@router.post("/passkey/register", name="passkey_register")
async def passkey_register(
    request: Request,
    credential: str = Form(...),
    name: str = Form(DEFAULT_PASSKEY_NAME),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Verify and store a newly created passkey."""
    try:
        parsed = json.loads(credential)
        response_fields = parsed["response"]
        registered = verify_registration(
            b64url_decode(response_fields["clientDataJSON"]),
            b64url_decode(response_fields["attestationObject"]),
            challenge_from_cookie(request.cookies.get(CHALLENGE_COOKIE_NAME)),
            _rp_id(request),
            _origin(request),
        )
    except (WebAuthnError, KeyError, TypeError, ValueError) as error:
        logger.warning("Passkey registration rejected for user %s: %s", current_user.id, error)
        raise HTTPException(status_code=400, detail="Passkey registration failed") from error

    if db.query(Passkey).filter(Passkey.credential_id == registered.credential_id).first():
        raise HTTPException(status_code=400, detail="Passkey already registered")

    db.add(
        Passkey(
            user_id=current_user.id,
            credential_id=registered.credential_id,
            public_key=registered.public_key,
            sign_count=registered.sign_count,
            name=(name or DEFAULT_PASSKEY_NAME).strip()[:100] or DEFAULT_PASSKEY_NAME,
        )
    )
    db.commit()

    log_auth_event(
        event_type="passkey_register",
        username=current_user.username,
        user_id=current_user.id,
        success=True,
    )

    response = JSONResponse({"redirect": "/profile"})
    response.delete_cookie(CHALLENGE_COOKIE_NAME)
    return response


@router.post("/passkey/login/options", name="passkey_login_options")
async def passkey_login_options(request: Request):
    """Return request options for a usernameless passkey login."""
    challenge, cookie_value = new_challenge()
    return _challenge_response(
        {
            "challenge": challenge,
            "rpId": _rp_id(request),
            # Empty: the credential is discoverable, so the authenticator picks it.
            "allowCredentials": [],
            "userVerification": "required",
            "timeout": CHALLENGE_TTL_SECONDS * 1000,
        },
        cookie_value,
    )


@router.post("/passkey/login", name="passkey_login")
async def passkey_login(
    request: Request,
    credential: str = Form(...),
    db: Session = Depends(get_db),
):
    """Verify a passkey assertion and issue the session."""
    ip = request.client.host if request.client else "unknown"

    try:
        parsed = json.loads(credential)
        credential_id = parsed["id"]
        response_fields = parsed["response"]
        client_data = b64url_decode(response_fields["clientDataJSON"])
        authenticator_data = b64url_decode(response_fields["authenticatorData"])
        signature = b64url_decode(response_fields["signature"])
        expected_challenge = challenge_from_cookie(request.cookies.get(CHALLENGE_COOKIE_NAME))
    except (WebAuthnError, KeyError, TypeError, ValueError) as error:
        logger.warning("Malformed passkey assertion from %s: %s", ip, error)
        raise HTTPException(status_code=401, detail="Passkey login failed") from error

    passkey = db.query(Passkey).filter(Passkey.credential_id == credential_id).first()
    if passkey is None:
        log_auth_event(
            event_type="passkey_login",
            username="unknown",
            success=False,
            details={"ip": ip, "reason": "unknown_credential"},
        )
        raise HTTPException(status_code=401, detail="Passkey login failed")

    try:
        sign_count = verify_assertion(
            client_data,
            authenticator_data,
            signature,
            passkey.public_key,
            passkey.sign_count,
            expected_challenge,
            _rp_id(request),
            _origin(request),
        )
    except WebAuthnError as error:
        logger.warning("Passkey assertion rejected for credential %s: %s", credential_id, error)
        log_auth_event(
            event_type="passkey_login",
            username=passkey.user.username,
            user_id=passkey.user_id,
            success=False,
            details={"ip": ip, "reason": "verification_failed"},
        )
        raise HTTPException(status_code=401, detail="Passkey login failed") from error

    user = passkey.user
    passkey.sign_count = sign_count
    passkey.last_used_at = utcnow()
    db.commit()

    log_auth_event(
        event_type="passkey_login",
        username=user.username,
        user_id=user.id,
        success=True,
        details={"ip": ip, "must_change_password": user.must_change_password == 1},
    )
    set_user_context(user_id=user.id, username=user.username)
    add_breadcrumb(message=f"User {user.username} logged in with a passkey", category="auth", level="info")

    destination = "/change-password" if user.must_change_password == 1 else "/"
    response = JSONResponse({"redirect": destination})
    set_auth_cookie(response, create_access_token(data={"sub": str(user.id)}))
    response.delete_cookie(CHALLENGE_COOKIE_NAME)
    return response


@router.post("/profile/passkey/{passkey_id}/delete", name="passkey_delete")
async def passkey_delete(
    passkey_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Delete one of the current user's passkeys.

    Scoped by user_id in the query rather than checked afterwards, so another
    user's ID returns 404 and never reveals that the row exists.
    """
    passkey = (
        db.query(Passkey)
        .filter(Passkey.id == passkey_id, Passkey.user_id == current_user.id)
        .first()
    )
    if passkey is None:
        raise HTTPException(status_code=404, detail="Passkey not found")

    db.delete(passkey)
    db.commit()

    log_auth_event(
        event_type="passkey_delete",
        username=current_user.username,
        user_id=current_user.id,
        success=True,
    )

    return RedirectResponse(url="/profile", status_code=302)
```

Then register the router in `app/main.py`. Add the import alongside the other route imports:

```python
from app.routes.passkey_routes import router as passkey_router
```

and the registration next to `app.include_router(auth_router)`:

```python
app.include_router(passkey_router)
```

Match the exact import style already used in that file (check whether it uses `from app.routes.X import router as X_router` or another form, and follow it).

- [ ] **Step 4: Run test to verify it passes**

Run: `venv/bin/python3 -m pytest tests/test_passkey_routes.py -v`
Expected: PASS, 12 tests.

If `test_login_options_sets_a_challenge_cookie` fails on the cookie assertion, check whether the TestClient exposes `response.cookies` for cookies set on a JSONResponse; assert on the `set-cookie` header instead.

- [ ] **Step 5: Run the full suite**

Run: `venv/bin/python3 -m pytest -q`
Expected: PASS.

- [ ] **Step 6: Lint**

Run: `venv/bin/python3 -m ruff check . && venv/bin/python3 -m ruff format --check .`
Expected: no findings.

- [ ] **Step 7: Commit**

```bash
git add app/routes/passkey_routes.py app/main.py tests/test_passkey_routes.py
git commit -m "feat(auth): add passkey registration and login routes"
```

---

## Task 6: Translations

**Files:**
- Modify: `app/core/translations.py`

**Interfaces:**
- Consumes: nothing
- Produces: the translation keys Tasks 7 and 8 render

- [ ] **Step 1: Add the Swedish keys**

In `app/core/translations.py`, inside the `"sv"` dict, next to the existing `profile_api_key_*` keys (around line 108):

```python
        "login_passkey_button": "Logga in med passkey",
        "login_passkey_failed": "Inloggning med passkey misslyckades.",
        "profile_passkey_title": "Passkeys",
        "profile_passkey_desc": "Logga in med Face ID, Touch ID, Windows Hello eller en säkerhetsnyckel i stället för lösenord. Lösenordet fortsätter att fungera.",
        "profile_passkey_none": "Inga passkeys registrerade.",
        "profile_passkey_add": "Lägg till passkey",
        "profile_passkey_name": "Namn",
        "profile_passkey_created": "Registrerad",
        "profile_passkey_last_used": "Senast använd",
        "profile_passkey_never_used": "Aldrig",
        "profile_passkey_delete": "Ta bort",
        "profile_passkey_delete_confirm": "Ta bort denna passkey? Du kan inte längre logga in med den enheten.",
        "profile_passkey_add_failed": "Kunde inte registrera passkey.",
        "profile_passkey_unsupported": "Den här webbläsaren stöder inte passkeys, eller så saknas HTTPS.",
```

- [ ] **Step 2: Add the English keys**

Inside the `"en"` dict, at the matching position (around line 1261):

```python
        "login_passkey_button": "Log in with a passkey",
        "login_passkey_failed": "Passkey login failed.",
        "profile_passkey_title": "Passkeys",
        "profile_passkey_desc": "Log in with Face ID, Touch ID, Windows Hello or a security key instead of a password. Your password keeps working.",
        "profile_passkey_none": "No passkeys registered.",
        "profile_passkey_add": "Add a passkey",
        "profile_passkey_name": "Name",
        "profile_passkey_created": "Registered",
        "profile_passkey_last_used": "Last used",
        "profile_passkey_never_used": "Never",
        "profile_passkey_delete": "Delete",
        "profile_passkey_delete_confirm": "Delete this passkey? You will no longer be able to log in with that device.",
        "profile_passkey_add_failed": "Could not register the passkey.",
        "profile_passkey_unsupported": "This browser does not support passkeys, or HTTPS is missing.",
```

- [ ] **Step 3: Verify both languages have every key**

Run:

```bash
venv/bin/python3 -c "
from app.core.translations import TRANSLATIONS
sv, en = set(TRANSLATIONS['sv']), set(TRANSLATIONS['en'])
assert sv == en, f'sv only: {sorted(sv - en)}, en only: {sorted(en - sv)}'
print('translation keys match')
"
```

Expected: `translation keys match`

If the repo already has a test asserting key parity between the two dicts, run that instead. Check with `grep -rn "TRANSLATIONS" tests/`.

- [ ] **Step 4: Lint and commit**

```bash
venv/bin/python3 -m ruff check app/core/translations.py && venv/bin/python3 -m ruff format --check app/core/translations.py
git add app/core/translations.py
git commit -m "feat(i18n): add passkey strings in Swedish and English"
```

---

## Task 7: The browser glue

**Files:**
- Create: `app/static/js/passkey.js`
- Modify: `app/templates/login.html`

**Interfaces:**
- Consumes: `/passkey/login/options`, `/passkey/login`, `/passkey/register/options`, `/passkey/register` (Task 5); translation keys (Task 6)
- Produces: global functions `periodicalPasskeyLogin()` and `periodicalPasskeyRegister()` used by Task 8's profile page

- [ ] **Step 1: Write the JavaScript**

Create `app/static/js/passkey.js`:

```javascript
// app/static/js/passkey.js
// WebAuthn glue. The server speaks urlencoded form bodies, not JSON, because
// CSRFMiddleware reads its token out of form bodies and rejects anything else.

(function () {
    "use strict";

    // Browsers hand out ArrayBuffers and expect ArrayBuffers, while the server
    // speaks base64url. These two conversions are the whole impedance mismatch.
    function fromBase64Url(value) {
        const padded = value.replace(/-/g, "+").replace(/_/g, "/");
        const raw = atob(padded + "=".repeat((4 - (padded.length % 4)) % 4));
        return Uint8Array.from(raw, function (character) {
            return character.charCodeAt(0);
        });
    }

    function toBase64Url(buffer) {
        const bytes = new Uint8Array(buffer);
        let raw = "";
        for (let index = 0; index < bytes.length; index += 1) {
            raw += String.fromCharCode(bytes[index]);
        }
        return btoa(raw).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
    }

    function csrfToken() {
        const field = document.querySelector("input[name='csrf_token']");
        return field ? field.value : "";
    }

    async function post(url, fields) {
        const body = new URLSearchParams(Object.assign({ csrf_token: csrfToken() }, fields || {}));
        const response = await fetch(url, {
            method: "POST",
            body: body,
            credentials: "same-origin",
        });
        if (!response.ok) {
            throw new Error("Request to " + url + " failed with " + response.status);
        }
        return response.json();
    }

    async function login() {
        const options = await post("/passkey/login/options");
        const credential = await navigator.credentials.get({
            publicKey: {
                challenge: fromBase64Url(options.challenge),
                rpId: options.rpId,
                allowCredentials: [],
                userVerification: options.userVerification,
                timeout: options.timeout,
            },
        });
        if (!credential) {
            throw new Error("No credential returned");
        }

        const result = await post("/passkey/login", {
            credential: JSON.stringify({
                id: credential.id,
                response: {
                    clientDataJSON: toBase64Url(credential.response.clientDataJSON),
                    authenticatorData: toBase64Url(credential.response.authenticatorData),
                    signature: toBase64Url(credential.response.signature),
                },
            }),
        });
        window.location.assign(result.redirect);
    }

    async function register(name) {
        const options = await post("/passkey/register/options");
        const credential = await navigator.credentials.create({
            publicKey: {
                challenge: fromBase64Url(options.challenge),
                rp: options.rp,
                user: {
                    // The user handle is opaque bytes to the authenticator.
                    id: new TextEncoder().encode(options.user.id),
                    name: options.user.name,
                    displayName: options.user.displayName,
                },
                pubKeyCredParams: options.pubKeyCredParams,
                authenticatorSelection: options.authenticatorSelection,
                attestation: options.attestation,
                timeout: options.timeout,
            },
        });
        if (!credential) {
            throw new Error("No credential created");
        }

        const result = await post("/passkey/register", {
            name: name || "",
            credential: JSON.stringify({
                id: credential.id,
                response: {
                    clientDataJSON: toBase64Url(credential.response.clientDataJSON),
                    attestationObject: toBase64Url(credential.response.attestationObject),
                },
            }),
        });
        window.location.assign(result.redirect);
    }

    function reportFailure(element, error) {
        console.error(error);
        const message = element && element.dataset.errorMessage;
        if (message) {
            window.alert(message);
        }
    }

    window.periodicalPasskeyLogin = login;
    window.periodicalPasskeyRegister = register;

    document.addEventListener("DOMContentLoaded", function () {
        const supported = Boolean(window.PublicKeyCredential);

        const loginButton = document.getElementById("passkey-login");
        if (loginButton) {
            // Hidden by default in the markup, so a browser without WebAuthn
            // never sees a button that cannot work.
            loginButton.hidden = !supported;
            loginButton.addEventListener("click", function () {
                login().catch(function (error) {
                    reportFailure(loginButton, error);
                });
            });
        }

        const registerButton = document.getElementById("passkey-register");
        if (registerButton) {
            registerButton.hidden = !supported;
            registerButton.addEventListener("click", function () {
                const field = document.getElementById("passkey-name");
                register(field ? field.value : "").catch(function (error) {
                    reportFailure(registerButton, error);
                });
            });
        }
    });
})();
```

- [ ] **Step 2: Wire up the login page**

In `app/templates/login.html`, change the password field's sibling markup. Add `webauthn` to the username field's autocomplete so browsers can offer the passkey from autofill, and add the button plus the script after the form.

Replace:

```html
                <input type="text" id="username" name="username" required autocomplete="username">
```

with:

```html
                <input type="text" id="username" name="username" required autocomplete="username webauthn">
```

and replace:

```html
            <button type="submit" class="btn login-submit">{{ t.login_button }}</button>
        </form>
```

with:

```html
            <button type="submit" class="btn login-submit">{{ t.login_button }}</button>
        </form>

        <button type="button" id="passkey-login" class="btn btn-secondary login-submit" hidden
                data-error-message="{{ t.login_passkey_failed }}">
            {{ t.login_passkey_button }}
        </button>
```

Then add the script tag just before the closing `{% endblock %}`:

```html
<script src="/static/js/passkey.js" defer></script>
```

The button sits outside the form deliberately: inside it, a browser without JavaScript would submit the login form when it is clicked.

- [ ] **Step 3: Check the page renders**

Run: `venv/bin/python3 -m pytest tests/ -q -k "login or base_layout"`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add app/static/js/passkey.js app/templates/login.html
git commit -m "feat(login): offer passkey sign-in on the login page"
```

---

## Task 8: Passkey management on the profile page

**Files:**
- Modify: `app/routes/profile.py` (the `profile_page` handler, around line 24-48)
- Modify: `app/templates/profile.html` (new section after the API key section, around line 288)

**Interfaces:**
- Consumes: `Passkey` (Task 1), `/profile/passkey/{id}/delete` (Task 5), translation keys (Task 6), `periodicalPasskeyRegister` (Task 7)
- Produces: nothing later tasks depend on

- [ ] **Step 1: Pass the passkeys into the template context**

In `app/routes/profile.py`, add `Passkey` to the existing import from `app.database.database`:

```python
from app.database.database import Absence, AbsenceType, Passkey, User, UserRole, WageType, get_db
```

and add one key to the context dict returned by `profile_page`:

```python
            "passkeys": db.query(Passkey)
            .filter(Passkey.user_id == current_user.id)
            .order_by(Passkey.created_at)
            .all(),
```

- [ ] **Step 2: Add the template section**

In `app/templates/profile.html`, immediately after the closing `</section>` of the API key block and before the `<!-- Kalenderprenumeration -->` comment:

```html
    <!-- Passkeys -->
    <section class="section-mt">
        <h2>{{ t.profile_passkey_title }}</h2>
        <div class="card">
            <p class="text-muted form-card-title">{{ t.profile_passkey_desc }}</p>

            {% if passkeys %}
            <table class="data-table">
                <thead>
                    <tr>
                        <th>{{ t.profile_passkey_name }}</th>
                        <th>{{ t.profile_passkey_created }}</th>
                        <th>{{ t.profile_passkey_last_used }}</th>
                        <th></th>
                    </tr>
                </thead>
                <tbody>
                    {% for passkey in passkeys %}
                    <tr>
                        <td>{{ passkey.name }}</td>
                        <td>{{ passkey.created_at.strftime('%Y-%m-%d') }}</td>
                        <td>
                            {% if passkey.last_used_at %}
                                {{ passkey.last_used_at.strftime('%Y-%m-%d') }}
                            {% else %}
                                {{ t.profile_passkey_never_used }}
                            {% endif %}
                        </td>
                        <td>
                            <form method="POST" action="/profile/passkey/{{ passkey.id }}/delete"
                                  class="inline-form"
                                  onsubmit="return confirm('{{ t.profile_passkey_delete_confirm }}');">
                                <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
                                <button type="submit" class="btn btn-danger">{{ t.profile_passkey_delete }}</button>
                            </form>
                        </td>
                    </tr>
                    {% endfor %}
                </tbody>
            </table>
            {% else %}
            <p class="text-muted">{{ t.profile_passkey_none }}</p>
            {% endif %}

            <div class="profile-actions">
                <input type="text" id="passkey-name" class="profile-api-input"
                       placeholder="{{ t.profile_passkey_name }}" maxlength="100">
                <button type="button" id="passkey-register" class="btn btn-primary" hidden
                        data-error-message="{{ t.profile_passkey_add_failed }}">
                    {{ t.profile_passkey_add }}
                </button>
            </div>
        </div>
    </section>
```

Check the class names against the rest of `profile.html` before committing: use whatever table class the wage history table already uses rather than inventing `data-table` if that class does not exist.

The hidden `csrf_token` field in the delete form is also what `passkey.js` reads for its fetch calls, so the profile page needs no separate token.

- [ ] **Step 3: Add the script tag**

At the end of `app/templates/profile.html`, inside the block, add:

```html
<script src="/static/js/passkey.js" defer></script>
```

If the template already has a `{% block scripts %}` or a trailing script area, put it there and follow the existing convention.

- [ ] **Step 4: Write a test that the section renders**

Append to `tests/test_passkey_routes.py`:

```python
def test_profile_page_lists_registered_passkeys(test_client, test_db, test_user):
    _register_credential(test_db, test_user)
    _login(test_client, test_user)

    response = test_client.get("/profile")

    assert response.status_code == 200
    assert "Testnyckel" in response.text
    assert "/static/js/passkey.js" in response.text
```

- [ ] **Step 5: Run the tests**

Run: `venv/bin/python3 -m pytest tests/test_passkey_routes.py -v`
Expected: PASS, 13 tests.

- [ ] **Step 6: Run the full suite and lint**

Run: `venv/bin/python3 -m pytest -q && venv/bin/python3 -m ruff check . && venv/bin/python3 -m ruff format --check .`
Expected: all PASS, no lint findings.

- [ ] **Step 7: Commit**

```bash
git add app/routes/profile.py app/templates/profile.html tests/test_passkey_routes.py
git commit -m "feat(profile): manage passkeys from the profile page"
```

---

## Task 9: Manual verification and changelog

**Files:**
- Modify: `CHANGELOG.md`

WebAuthn cannot be exercised by the test suite end to end, because it needs a real authenticator and a secure context. This task is the manual check that the browser side actually works.

- [ ] **Step 1: Run the app on localhost**

Run: `venv/bin/python3 -m uvicorn app.main:app --host 127.0.0.1 --port 8000`

`127.0.0.1` and `localhost` are treated as secure contexts by browsers, which is what makes this testable without TLS. Do **not** try this against `http://192.168.0.190:8001`: WebAuthn is unavailable there and every call will fail.

- [ ] **Step 2: Apply the schema change to the dev database**

Run: `venv/bin/python3 migrations/migrate_schema.py`
Expected: `Creating table passkeys`

- [ ] **Step 3: Register a passkey**

Open `http://localhost:8000/profile`, log in with a password, type a name, click "Lägg till passkey" and complete the platform prompt. Confirm the row appears in the table with today's date and "Aldrig" under last used.

- [ ] **Step 4: Log in with it**

Log out, open `http://localhost:8000/login`, click "Logga in med passkey", complete the prompt. Confirm you land on `/`. Reload `/profile` and confirm the last-used date is now filled in.

- [ ] **Step 5: Delete it**

Click "Ta bort", accept the confirmation, confirm the row is gone and that the passkey no longer logs you in.

- [ ] **Step 6: Update the changelog**

Read the top of `CHANGELOG.md` to match the existing format exactly (heading level, date style, section names), then add an entry describing passkey login as a new alternative to password login, noting that passwords keep working and that a passkey is bound to the domain it was registered on.

- [ ] **Step 7: Final full check**

```bash
venv/bin/python3 -m ruff check .
venv/bin/python3 -m ruff format --check .
venv/bin/python3 -m pytest -q
```

Expected: all clean.

- [ ] **Step 8: Commit**

```bash
git add CHANGELOG.md
git commit -m "docs(changelog): note passkey login"
```

- [ ] **Step 9: Push and open the PR**

SSH is refused in agent sessions, so push over HTTPS with the gh credential helper:

```bash
git -c credential.helper='!gh auth git-credential' push -u origin feat/passkey-login
```

Open the PR with `gh pr create`. Title and body in English. No AI attribution, no em dashes.

The PR body must include this deploy note:

> Run `python migrations/migrate_schema.py` on prod before tagging a release. Deploy does not run migrations, and the app will fail on the missing `passkeys` table without it.

---

## Notes for whoever executes this

**The dev instance cannot test this feature.** `http://192.168.0.190:8001` is not a secure context, so `window.PublicKeyCredential` is undefined there and the buttons stay hidden. Use `localhost` or the TLS dev domain.

**Passkeys are bound to a hostname.** One registered against `localhost` will not work against the prod domain, and vice versa. That is WebAuthn working correctly, not a bug to chase.

**Do not add a dependency.** If a task feels like it needs `cbor2` or `webauthn`, re-read Task 2: the parsing needed is already there.
