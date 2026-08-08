# app/auth/auth.py
"""
Authentication and authorization utilities.
"""

import base64
import calendar
import hashlib
import hmac
import json
import os
from datetime import datetime, timedelta

import bcrypt
from cryptography.fernet import Fernet, InvalidToken
from fastapi import Depends, HTTPException, Request, Response, status
from fastapi.security import HTTPBearer
from sqlalchemy.orm import Session

from app.database.database import LoginAttempt, User, UserRole, get_db, utcnow

# Configuration - reads from environment variables for security
SECRET_KEY = os.getenv("SECRET_KEY", "your-secret-key-change-this-in-production")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24 * 7  # 7 dagar

# Validate SECRET_KEY in production
is_production = os.getenv("PRODUCTION", "false").lower() == "true"
INSECURE_SECRET_KEYS = {
    "your-secret-key-change-this-in-production",
    "change-me-to-random-secret",
}
if is_production and (SECRET_KEY in INSECURE_SECRET_KEYS or len(SECRET_KEY) < 32):
    raise RuntimeError("SECRET_KEY must be set to a strong random value in production!")
if not is_production and SECRET_KEY in INSECURE_SECRET_KEYS:
    import warnings

    warnings.warn(
        "WARNING: Using default SECRET_KEY! Set SECRET_KEY environment variable for production.",
        RuntimeWarning,
        stacklevel=2,
    )


class PasswordChangeRequired(Exception):
    """Raised when an authenticated user must change their password before proceeding.

    Translated into a redirect to /change-password by an exception handler in main.py.
    Lets the enforcement live in the auth dependencies (centralised, testable) instead
    of in middleware that bypasses the get_db override.
    """


def _require_password_change(user: "User | None") -> None:
    """Raise PasswordChangeRequired when the user has a pending mandatory password change."""
    if user is not None and user.must_change_password == 1:
        raise PasswordChangeRequired()


# Login brute-force protection
# After LOGIN_MAX_ATTEMPTS failures for the same (username, ip) within LOGIN_WINDOW_MINUTES,
# further attempts are rejected until the oldest failure ages out of the window.
LOGIN_MAX_ATTEMPTS = 5
LOGIN_WINDOW_MINUTES = 15


def _login_window_start() -> datetime:
    return utcnow() - timedelta(minutes=LOGIN_WINDOW_MINUTES)


def is_login_locked(db: Session, username: str, ip: str) -> bool:
    """Return True when (username, ip) has reached the failed-attempt limit in the window."""
    recent = (
        db.query(LoginAttempt)
        .filter(
            LoginAttempt.username == username,
            LoginAttempt.ip == ip,
            LoginAttempt.created_at >= _login_window_start(),
        )
        .count()
    )
    return recent >= LOGIN_MAX_ATTEMPTS


def record_failed_login(db: Session, username: str, ip: str) -> None:
    """Record a failed attempt and prune attempts for this key that fell out of the window."""
    db.add(LoginAttempt(username=username, ip=ip))
    db.query(LoginAttempt).filter(
        LoginAttempt.username == username,
        LoginAttempt.ip == ip,
        LoginAttempt.created_at < _login_window_start(),
    ).delete(synchronize_session=False)
    db.commit()


def clear_login_attempts(db: Session, username: str, ip: str) -> None:
    """Clear recorded attempts for a key, called after a successful login."""
    db.query(LoginAttempt).filter(
        LoginAttempt.username == username,
        LoginAttempt.ip == ip,
    ).delete(synchronize_session=False)
    db.commit()


# Password hashing. bcrypt is called directly rather than through passlib's
# CryptContext: the app has only ever used one scheme, and passlib has been
# unmaintained since 2020 to the point of logging a traceback on import because
# it reads a bcrypt attribute that no longer exists. The hashes are the same
# `$2b$12$` strings at the same cost factor, so stored hashes keep verifying.
_BCRYPT_ROUNDS = 12

# Token scheme
security = HTTPBearer(auto_error=False)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a password against its hash.

    A hash bcrypt cannot parse counts as a failed login rather than an error:
    a corrupt or foreign value in the column should not turn the login page
    into a 500.
    """
    try:
        return bcrypt.checkpw(plain_password.encode("utf-8"), hashed_password.encode("utf-8"))
    except ValueError:
        return False


def get_password_hash(password: str) -> str:
    """Generate password hash."""
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=_BCRYPT_ROUNDS)).decode("utf-8")


# Precomputed hash used to equalise authentication timing when the username does not exist,
# so an attacker cannot tell valid usernames apart by measuring response time.
_DUMMY_PASSWORD_HASH = get_password_hash("timing-equalisation-placeholder")


# --- JWT (HS256) ---
#
# Signed with SECRET_KEY the same way the CSRF token is, using hmac from the
# standard library. This replaced python-jose, which pulled in ecdsa, rsa,
# pyasn1 and six to serve one symmetric algorithm with one key, and has a
# history of algorithm-confusion advisories in the parts this app never used.
# The wire format is unchanged, so tokens issued before the swap still validate
# and nobody is logged out by deploying it.
#
# The header is a fixed constant rather than something parsed and honoured: the
# algorithm is chosen here, never taken from the token, which is what makes
# "alg": "none" and HS256/RS256 confusion impossible by construction.
_JWT_HEADER = '{"alg":"HS256","typ":"JWT"}'


def _b64url_encode(raw: bytes) -> str:
    """Base64url without padding, as JWT requires."""
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64url_decode(segment: str) -> bytes:
    """Reverse of _b64url_encode, restoring the padding it stripped."""
    return base64.urlsafe_b64decode(segment.encode("ascii") + b"=" * (-len(segment) % 4))


def _jwt_signature(signing_input: str) -> bytes:
    """HMAC-SHA256 over "<header>.<payload>", the bytes actually sent."""
    return hmac.new(SECRET_KEY.encode("utf-8"), signing_input.encode("ascii"), hashlib.sha256).digest()


def create_access_token(data: dict, expires_delta: timedelta | None = None) -> str:
    """Create a JWT access token."""
    to_encode = data.copy()
    expire = utcnow() + (expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
    # utcnow() is naive UTC, so the timestamp has to be read as UTC. datetime.timestamp()
    # would read a naive value as local time and shift every expiry by the Stockholm offset.
    to_encode["exp"] = calendar.timegm(expire.utctimetuple())

    header = _b64url_encode(_JWT_HEADER.encode("ascii"))
    payload = _b64url_encode(json.dumps(to_encode, separators=(",", ":")).encode("utf-8"))
    signing_input = f"{header}.{payload}"
    return f"{signing_input}.{_b64url_encode(_jwt_signature(signing_input))}"


def decode_token(token: str) -> dict | None:
    """Decode and validate a JWT token, returning None if it is not usable.

    Rejects anything that is not a well-formed HS256 token signed with this
    deployment's SECRET_KEY, whose `exp` has not passed and whose `sub` is a
    string. A token without `exp` is accepted, which is what the tokens issued
    for tests rely on.
    """
    parts = token.split(".")
    if len(parts) != 3:
        return None
    header_b64, payload_b64, signature_b64 = parts

    try:
        header = json.loads(_b64url_decode(header_b64))
        signature = _b64url_decode(signature_b64)
    except (ValueError, UnicodeError):
        return None

    if not isinstance(header, dict) or header.get("alg") != ALGORITHM:
        return None

    expected = _jwt_signature(f"{header_b64}.{payload_b64}")
    if not hmac.compare_digest(expected, signature):
        return None

    try:
        payload = json.loads(_b64url_decode(payload_b64))
    except (ValueError, UnicodeError):
        return None
    if not isinstance(payload, dict):
        return None

    exp = payload.get("exp")
    if exp is not None:
        if not isinstance(exp, (int, float)) or isinstance(exp, bool):
            return None
        if calendar.timegm(utcnow().utctimetuple()) >= exp:
            return None

    if "sub" in payload and not isinstance(payload["sub"], str):
        return None

    return payload


def get_user_by_username(db: Session, username: str) -> User | None:
    """Get user by username."""
    return db.query(User).filter(User.username == username).first()


def get_user_by_id(db: Session, user_id: int) -> User | None:
    """Get user by ID."""
    return db.query(User).filter(User.id == user_id).first()


def authenticate_user(db: Session, username: str, password: str) -> User | None:
    """
    Authenticate a user with username and password.

    NOTE: Inactive users (is_active=0) are allowed to log in.
    This enables former employees to view their historical data after leaving.
    Access to current data is controlled by employment period filtering in views.
    """
    user = get_user_by_username(db, username)
    if not user:
        # Verify against a dummy hash so a missing user costs ~the same time as a wrong
        # password, preventing username enumeration via response timing.
        verify_password(password, _DUMMY_PASSWORD_HASH)
        return None
    if not verify_password(password, user.password_hash):
        return None
    return user


async def get_current_user_from_cookie(request: Request, db: Session = Depends(get_db)) -> User | None:
    """Extract and validate user from cookie."""
    token = request.cookies.get("access_token")
    if not token:
        return None

    # Remove "Bearer " prefix if present
    if token.startswith("Bearer "):
        token = token[7:]

    payload = decode_token(token)
    if payload is None:
        return None

    user_id: int = payload.get("sub")
    if user_id is None:
        return None

    user = get_user_by_id(db, int(user_id))
    return user


async def get_current_user_allow_pwd_change(request: Request, db: Session = Depends(get_db)) -> User:
    """Get current authenticated user WITHOUT enforcing a pending password change.

    Used by the change-password and logout routes so a user with must_change_password=1
    can actually reach them instead of being redirected back in a loop.
    """
    user = await get_current_user_from_cookie(request, db)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


async def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    """Get current authenticated user. Raises 401 if not authenticated.

    Enforces a pending mandatory password change by raising PasswordChangeRequired.
    """
    user = await get_current_user_allow_pwd_change(request, db)
    _require_password_change(user)
    return user


async def get_current_user_optional(request: Request, db: Session = Depends(get_db)) -> User | None:
    """Get current user if authenticated, None otherwise.

    Still enforces a pending mandatory password change for authenticated users.
    """
    user = await get_current_user_from_cookie(request, db)
    _require_password_change(user)
    return user


async def get_admin_user(current_user: User = Depends(get_current_user)) -> User:
    """Get current user and verify admin role."""
    if current_user.role != UserRole.ADMIN:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")
    return current_user


def set_auth_cookie(response: Response, token: str) -> None:
    """Set authentication cookie."""
    # Use secure cookies in production (requires HTTPS)
    is_production = os.getenv("PRODUCTION", "false").lower() == "true"

    response.set_cookie(
        key="access_token",
        value=f"Bearer {token}",
        httponly=True,
        max_age=ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        samesite="lax",
        secure=is_production,  # True in production with HTTPS
    )


def clear_auth_cookie(response: Response) -> None:
    """Clear authentication cookie."""
    response.delete_cookie(key="access_token")


def hash_api_key(api_key: str) -> str:
    """Return the SHA-256 hex digest of an API key.

    Only the digest is used for authentication lookups, so a leaked database
    does not expose usable keys. SHA-256 (rather than bcrypt) keeps lookups
    indexable and is sufficient because keys are high-entropy random tokens.
    """
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()


# Fernet key derived from SECRET_KEY, used to encrypt API keys at rest so the
# profile page can still display them. Lookups always go through hash_api_key;
# the encrypted copy exists only for display.
def _api_key_fernet() -> Fernet:
    digest = hashlib.sha256(SECRET_KEY.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_api_key(api_key: str) -> str:
    """Encrypt an API key for at-rest storage (display purposes only)."""
    return _api_key_fernet().encrypt(api_key.encode("utf-8")).decode("utf-8")


def decrypt_api_key(token: str | None) -> str | None:
    """Decrypt a stored API key, or None if missing or undecryptable.

    Returns None when SECRET_KEY has changed since the key was encrypted; the
    key still works for authentication (hash lookup) but cannot be displayed.
    """
    if not token:
        return None
    try:
        return _api_key_fernet().decrypt(token.encode("utf-8")).decode("utf-8")
    except InvalidToken:
        return None


async def get_api_user(request: Request, db: Session = Depends(get_db)) -> User:
    """Authenticate via Bearer API key in Authorization header."""
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="API key required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    api_key = auth_header[7:]
    user = db.query(User).filter(User.api_key == hash_api_key(api_key)).first()
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


async def get_admin_api_user(user: User = Depends(get_api_user)) -> User:
    """Get API user and verify admin role."""
    if user.role != UserRole.ADMIN:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")
    return user
