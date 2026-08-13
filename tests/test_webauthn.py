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
