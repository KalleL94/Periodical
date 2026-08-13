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
