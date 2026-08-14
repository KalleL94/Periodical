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

import base64
import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass
from urllib.parse import urlparse

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa

from app.auth.auth import SECRET_KEY


class WebAuthnError(Exception):
    """Any malformed, unsupported or failing WebAuthn input.

    One exception type for every rejection: callers turn it into a 400 or 401,
    and the distinction between "malformed CBOR" and "bad signature" is not
    something an unauthenticated client should be told.
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
    message = f"{nonce}.{expiry}".encode()
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

    if not hmac.compare_digest(authenticator_data[:_RP_ID_HASH_LENGTH], hashlib.sha256(rp_id.encode("utf-8")).digest()):
        raise WebAuthnError("Authenticator data is for a different relying party")

    flags = authenticator_data[_FLAGS_OFFSET]
    if not flags & FLAG_USER_PRESENT:
        raise WebAuthnError("User presence flag is not set")
    if not flags & FLAG_USER_VERIFIED:
        raise WebAuthnError("User verification flag is not set")

    sign_count = int.from_bytes(authenticator_data[_SIGN_COUNT_OFFSET:_ATTESTED_DATA_OFFSET], "big")
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
    if (sign_count != 0 or stored_sign_count != 0) and sign_count <= stored_sign_count:
        raise WebAuthnError("Signature counter did not advance")

    public_key = load_cose_key(b64url_decode(stored_public_key))
    verify_signature(public_key, authenticator_data + hashlib.sha256(client_data_json).digest(), signature)
    return sign_count
