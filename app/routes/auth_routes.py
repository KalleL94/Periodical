# app/routes/auth_routes.py
"""
Authentication routes: login, logout, change-password.
"""

from urllib.parse import urlparse

from fastapi import APIRouter, Depends, Form, Query, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.auth.auth import (
    authenticate_user,
    clear_auth_cookie,
    create_access_token,
    get_current_user,
    get_current_user_optional,
    get_password_hash,
    set_auth_cookie,
)
from app.core.logging_config import get_logger
from app.core.request_logging import log_auth_event
from app.core.schedule import clear_schedule_cache
from app.core.sentry_config import add_breadcrumb, clear_user_context, set_user_context
from app.database.database import User, get_db
from app.routes.shared import templates

logger = get_logger(__name__)

router = APIRouter(tags=["auth"])


def is_safe_redirect(url: str) -> bool:
    """Check if redirect URL is safe (local path only)."""
    if not url:
        return False
    parsed = urlparse(url)
    return not parsed.scheme and not parsed.netloc and url.startswith("/") and not url.startswith("//")


@router.get("/login", response_class=HTMLResponse, name="login_page")
async def login_page(
    request: Request,
    next: str | None = Query(None),
    current_user: User | None = Depends(get_current_user_optional),
):
    """Show login form."""
    if current_user:
        return RedirectResponse(url="/", status_code=302)
    return templates.TemplateResponse(
        "login.html", {"request": request, "next": next if is_safe_redirect(next) else None}
    )


@router.post("/login", name="login")
async def login(
    request: Request,
    response: Response,
    username: str = Form(...),
    password: str = Form(...),
    next: str = Form(None),
    db: Session = Depends(get_db),
):
    """Process login form."""
    user = authenticate_user(db, username, password)
    if not user:
        log_auth_event(
            event_type="login",
            username=username,
            success=False,
            details={"ip": request.client.host if request.client else "unknown"},
        )

        return templates.TemplateResponse(
            "login.html",
            {"request": request, "error": "Fel användarnamn eller lösenord", "next": next},
            status_code=401,
        )

    log_auth_event(
        event_type="login",
        username=user.username,
        user_id=user.id,
        success=True,
        details={
            "ip": request.client.host if request.client else "unknown",
            "must_change_password": user.must_change_password == 1,
        },
    )

    set_user_context(user_id=user.id, username=user.username)
    add_breadcrumb(message=f"User {user.username} logged in", category="auth", level="info")

    access_token = create_access_token(data={"sub": str(user.id)})

    if user.must_change_password == 1:
        redirect = RedirectResponse(url="/change-password", status_code=302)
        set_auth_cookie(redirect, access_token)
        return redirect

    redirect_url = next if is_safe_redirect(next) else "/"
    redirect = RedirectResponse(url=redirect_url, status_code=302)
    set_auth_cookie(redirect, access_token)
    return redirect


@router.get("/logout", name="logout")
async def logout(response: Response, current_user: User | None = Depends(get_current_user_optional)):
    """Log out user."""
    if current_user:
        log_auth_event(
            event_type="logout",
            username=current_user.username,
            user_id=current_user.id,
            success=True,
        )
        clear_user_context()

    redirect = RedirectResponse(url="/login", status_code=302)
    clear_auth_cookie(redirect)
    return redirect


@router.get("/change-password", response_class=HTMLResponse, name="change_password_page")
async def change_password_page(
    request: Request,
    current_user: User = Depends(get_current_user),
):
    """Show mandatory password change page for users with must_change_password=1."""
    return templates.TemplateResponse(
        "change_password.html",
        {
            "request": request,
            "user": current_user,
            "must_change": current_user.must_change_password == 1,
        },
    )


@router.post("/change-password", name="change_password_submit")
async def change_password_submit(
    request: Request,
    current_password: str = Form(...),
    new_password: str = Form(...),
    confirm_password: str = Form(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Process mandatory password change."""
    from app.auth.auth import verify_password

    if not verify_password(current_password, current_user.password_hash):
        return templates.TemplateResponse(
            "change_password.html",
            {
                "request": request,
                "user": current_user,
                "must_change": current_user.must_change_password == 1,
                "error": "Fel nuvarande lösenord",
            },
            status_code=400,
        )

    if new_password != confirm_password:
        return templates.TemplateResponse(
            "change_password.html",
            {
                "request": request,
                "user": current_user,
                "must_change": current_user.must_change_password == 1,
                "error": "Nya lösenordet matchar inte bekräftelsen",
            },
            status_code=400,
        )

    if verify_password(new_password, current_user.password_hash):
        return templates.TemplateResponse(
            "change_password.html",
            {
                "request": request,
                "user": current_user,
                "must_change": current_user.must_change_password == 1,
                "error": "Nytt lösenord måste vara annorlunda än det gamla",
            },
            status_code=400,
        )

    if len(new_password) < 8:
        return templates.TemplateResponse(
            "change_password.html",
            {
                "request": request,
                "user": current_user,
                "must_change": current_user.must_change_password == 1,
                "error": "Nytt lösenord måste vara minst 8 tecken",
            },
            status_code=400,
        )

    current_user.password_hash = get_password_hash(new_password)
    was_forced = current_user.must_change_password == 1
    current_user.must_change_password = 0

    try:
        db.commit()
    except Exception:
        db.rollback()
        raise

    clear_schedule_cache()

    log_auth_event(
        event_type="password_change",
        username=current_user.username,
        user_id=current_user.id,
        success=True,
        details={"forced": was_forced},
    )

    return RedirectResponse(url="/", status_code=302)
