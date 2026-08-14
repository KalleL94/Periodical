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

# Default label for a newly registered passkey when nothing better can be worked
# out. The user can name it when adding it.
DEFAULT_PASSKEY_NAME = "Passkey"

# Substring tables for labelling an unnamed passkey from the User-Agent.
#
# The authenticator's own identity would be better, and WebAuthn carries it as
# the AAGUID, but registration asks for `attestation: "none"` and the client
# then replaces the AAGUID with sixteen zero bytes. Asking for attestation to
# recover a display label would mean handling certificate chains and sending
# the authenticator model to the server, which is a poor trade for a name the
# user can type.
#
# Order matters in both tables and is the whole reason they are lists rather
# than dicts: Edge's User-Agent contains "Chrome", Chrome's contains "Safari",
# and iPadOS calls itself "Mac OS X". First match wins, most specific first.
_BROWSER_MARKERS = [
    ("Edg/", "Edge"),
    ("OPR/", "Opera"),
    ("Firefox/", "Firefox"),
    ("Chrome/", "Chrome"),
    ("Safari/", "Safari"),
]

_PLATFORM_MARKERS = [
    ("iPhone", "iPhone"),
    ("iPad", "iPad"),
    ("Android", "Android"),
    ("Windows", "Windows"),
    ("Mac OS X", "macOS"),
    ("Linux", "Linux"),
]


def device_label(user_agent: str) -> str:
    """Return a display name like "Chrome (macOS)" for an unnamed passkey.

    Falls back to DEFAULT_PASSKEY_NAME unless both halves are recognised: half a
    label ("Chrome" with no platform) is worse than a plain one when someone is
    looking at a list trying to work out which device to revoke. Product names
    are not translated, so the label reads the same in both languages.
    """
    browser = next((name for marker, name in _BROWSER_MARKERS if marker in user_agent), None)
    platform = next((name for marker, name in _PLATFORM_MARKERS if marker in user_agent), None)
    if browser is None or platform is None:
        return DEFAULT_PASSKEY_NAME
    return f"{browser} ({platform})"


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
            "pubKeyCredParams": [{"type": "public-key", "alg": algorithm} for algorithm in SUPPORTED_ALGORITHMS],
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
    # Empty rather than DEFAULT_PASSKEY_NAME: FastAPI treats an empty form value as
    # missing and substitutes the default, so a name default here would mask the
    # blank field the device label is meant to fill in.
    name: str = Form(""),
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
            name=name.strip()[:100] or device_label(request.headers.get("user-agent", "")),
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
    passkey = db.query(Passkey).filter(Passkey.id == passkey_id, Passkey.user_id == current_user.id).first()
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
