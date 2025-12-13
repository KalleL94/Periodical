# app/routes/auth_routes.py
"""
Authentication routes: login, logout, registration.
"""

from fastapi import APIRouter, Depends, HTTPException, status, Request, Response, Form, Query
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from pydantic import BaseModel
from urllib.parse import urlparse

from app.core.schedule import clear_schedule_cache
from app.database.database import get_db, User, UserRole
from app.auth.auth import (
    authenticate_user,
    create_access_token,
    get_password_hash,
    get_current_user,
    get_current_user_optional,
    get_admin_user,
    set_auth_cookie,
    clear_auth_cookie,
    get_user_by_username,
)

router = APIRouter(tags=["auth"])
templates = Jinja2Templates(directory="app/templates")


# ============ Pydantic schemas ============

class UserCreate(BaseModel):
    username: str
    password: str
    name: str
    wage: int
    role: UserRole = UserRole.USER


class UserUpdate(BaseModel):
    name: str | None = None
    wage: int | None = None
    vacation: dict | None = None


class PasswordChange(BaseModel):
    current_password: str
    new_password: str

def is_safe_redirect(url: str) -> bool:
    """Check if redirect URL is safe (local path only)."""
    if not url:
        return False
    parsed = urlparse(url)
    # Only allow relative paths (no scheme or netloc)
    return not parsed.scheme and not parsed.netloc and url.startswith("/")


# ============ HTML Routes ============

@router.get("/login", response_class=HTMLResponse, name="login_page")
async def login_page(
    request: Request,
    next: str | None = Query(None),
    current_user: User | None = Depends(get_current_user_optional)
):
    """Show login form."""
    if current_user:
        return RedirectResponse(url="/", status_code=302)
    return templates.TemplateResponse(
        "login.html", 
        {"request": request, "next": next if is_safe_redirect(next) else None}
    )


@router.post("/login", name="login")
async def login(
    request: Request,
    response: Response,
    username: str = Form(...),
    password: str = Form(...),
    next: str = Form(None),
    db: Session = Depends(get_db)
):
    """Process login form."""
    user = authenticate_user(db, username, password)
    if not user:
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "error": "Fel användarnamn eller lösenord", "next": next},
            status_code=401
        )

    # Create access token and set cookie
    access_token = create_access_token(data={"sub": str(user.id)})

    # Check if user must change password
    if user.must_change_password == 1:
        redirect = RedirectResponse(url="/change-password", status_code=302)
        set_auth_cookie(redirect, access_token)
        return redirect

    # Redirect to next URL if safe, otherwise home
    redirect_url = next if is_safe_redirect(next) else "/"
    redirect = RedirectResponse(url=redirect_url, status_code=302)
    set_auth_cookie(redirect, access_token)
    return redirect


@router.get("/logout", name="logout")
async def logout(response: Response):
    """Log out user."""
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
        }
    )


@router.post("/change-password", name="change_password_submit")
async def change_password_submit(
    request: Request,
    current_password: str = Form(...),
    new_password: str = Form(...),
    confirm_password: str = Form(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Process mandatory password change."""
    from app.auth.auth import verify_password

    # Validate current password
    if not verify_password(current_password, current_user.password_hash):
        return templates.TemplateResponse(
            "change_password.html",
            {
                "request": request,
                "user": current_user,
                "must_change": current_user.must_change_password == 1,
                "error": "Fel nuvarande lösenord"
            },
            status_code=400
        )

    # Validate new password matches confirmation
    if new_password != confirm_password:
        return templates.TemplateResponse(
            "change_password.html",
            {
                "request": request,
                "user": current_user,
                "must_change": current_user.must_change_password == 1,
                "error": "Nya lösenordet matchar inte bekräftelsen"
            },
            status_code=400
        )

    # Validate new password is different from old
    if verify_password(new_password, current_user.password_hash):
        return templates.TemplateResponse(
            "change_password.html",
            {
                "request": request,
                "user": current_user,
                "must_change": current_user.must_change_password == 1,
                "error": "Nytt lösenord måste vara annorlunda än det gamla"
            },
            status_code=400
        )

    # Validate new password strength (minimum 8 characters)
    if len(new_password) < 8:
        return templates.TemplateResponse(
            "change_password.html",
            {
                "request": request,
                "user": current_user,
                "must_change": current_user.must_change_password == 1,
                "error": "Nytt lösenord måste vara minst 8 tecken"
            },
            status_code=400
        )

    # Update password and clear must_change_password flag
    current_user.password_hash = get_password_hash(new_password)
    current_user.must_change_password = 0
    db.commit()
    clear_schedule_cache()

    # Redirect to home page
    return RedirectResponse(url="/", status_code=302)


@router.get("/profile", response_class=HTMLResponse, name="profile")
async def profile_page(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Show user profile page."""
    return templates.TemplateResponse(
        "profile.html",
        {
            "request": request,
            "user": current_user,
        }
    )


@router.post("/profile", name="profile_update")
async def update_profile(
    request: Request,
    name: str = Form(...),
    wage: int = Form(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Update user profile."""
    current_user.name = name
    current_user.wage = wage
    db.commit()
    
    return RedirectResponse(url="/profile", status_code=302)


@router.post("/profile/password", name="change_password")
async def change_password(
    request: Request,
    current_password: str = Form(...),
    new_password: str = Form(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Change user password."""
    from app.auth.auth import verify_password
    
    if not verify_password(current_password, current_user.password_hash):
        return templates.TemplateResponse(
            "profile.html",
            {"request": request, "user": current_user, "error": "Fel nuvarande lösenord"},
            status_code=400
        )
    
    current_user.password_hash = get_password_hash(new_password)
    db.commit()
    
    return RedirectResponse(url="/profile", status_code=302)


@router.get("/profile/vacation", response_class=HTMLResponse, name="vacation_page")
async def vacation_page(
    request: Request,
    year: int | None = None,
    current_user: User = Depends(get_current_user),
):
    """Show vacation management page."""
    import datetime
    if year is None:
        year = datetime.date.today().year
    
    vacation = current_user.vacation or {}
    vacation_weeks = vacation.get(str(year), [])
    
    return templates.TemplateResponse(
        "vacation.html",
        {
            "request": request,
            "user": current_user,
            "year": year,
            "vacation_weeks": vacation_weeks,
        }
    )


@router.post("/profile/vacation", name="update_vacation")
async def update_vacation(
    request: Request,
    year: int = Form(...),
    weeks: str = Form(""),  # Comma-separated week numbers
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Update vacation weeks for a year."""
    # Parse weeks from form
    if weeks.strip():
        week_list = [int(w.strip()) for w in weeks.split(",") if w.strip().isdigit()]
    else:
        week_list = []
    
    # Validate week numbers
    week_list = [w for w in week_list if 1 <= w <= 53]
    week_list = sorted(set(week_list))
    
    # Update vacation
    vacation = current_user.vacation or {}
    vacation[str(year)] = week_list
    current_user.vacation = vacation
    
    # SQLAlchemy needs this to detect JSON changes
    from sqlalchemy.orm.attributes import flag_modified
    flag_modified(current_user, "vacation")
    
    db.commit()
    clear_schedule_cache()
    
    return RedirectResponse(url=f"/profile/vacation?year={year}", status_code=302)


# ============ Admin Routes ============

@router.get("/admin/users", response_class=HTMLResponse, name="admin_users")
async def admin_users_page(
    request: Request,
    current_user: User = Depends(get_admin_user),
    db: Session = Depends(get_db)
):
    """Admin: list all users."""
    users = db.query(User).order_by(User.id).all()
    return templates.TemplateResponse(
        "admin_users.html",
        {
            "request": request,
            "user": current_user,
            "users": users,
        }
    )


@router.get("/admin/users/create", response_class=HTMLResponse, name="admin_create_user_page")
async def admin_create_user_page(
    request: Request,
    current_user: User = Depends(get_admin_user),
):
    """Admin: show create user form."""
    return templates.TemplateResponse(
        "admin_user_create.html",
        {
            "request": request,
            "user": current_user,
        }
    )


@router.post("/admin/users/create", name="admin_create_user")
async def admin_create_user(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    name: str = Form(...),
    wage: int = Form(...),
    role: str = Form("user"),
    current_user: User = Depends(get_admin_user),
    db: Session = Depends(get_db)
):
    """Admin: create new user."""
    # Check if username exists
    if get_user_by_username(db, username):
        return templates.TemplateResponse(
            "admin_user_create.html",
            {"request": request, "user": current_user, "error": "Användarnamnet finns redan"},
            status_code=400
        )
    
    new_user = User(
        username=username,
        password_hash=get_password_hash(password),
        name=name,
        wage=wage,
        role=UserRole(role),
        vacation={},
        must_change_password=1,  # Force password change on first login
    )
    db.add(new_user)
    db.commit()
    
    return RedirectResponse(url="/admin/users", status_code=302)


@router.get("/admin/users/{user_id}", response_class=HTMLResponse, name="admin_edit_user_page")
async def admin_edit_user_page(
    request: Request,
    user_id: int,
    current_user: User = Depends(get_admin_user),
    db: Session = Depends(get_db)
):
    """Admin: show edit user form."""
    edit_user = db.query(User).filter(User.id == user_id).first()
    if not edit_user:
        raise HTTPException(status_code=404, detail="User not found")
    
    return templates.TemplateResponse(
        "admin_user_edit.html",
        {
            "request": request,
            "user": current_user,
            "edit_user": edit_user,
        }
    )


@router.post("/admin/users/{user_id}", name="admin_update_user")
async def admin_update_user(
    request: Request,
    user_id: int,
    name: str = Form(...),
    wage: int = Form(...),
    role: str = Form("user"),
    new_password: str = Form(None),
    current_user: User = Depends(get_admin_user),
    db: Session = Depends(get_db)
):
    """Admin: update user."""
    edit_user = db.query(User).filter(User.id == user_id).first()
    if not edit_user:
        raise HTTPException(status_code=404, detail="User not found")
    
    edit_user.name = name
    edit_user.wage = wage
    edit_user.role = UserRole(role)
    
    if new_password:
        edit_user.password_hash = get_password_hash(new_password)
    
    db.commit()
    
    return RedirectResponse(url="/admin/users", status_code=302)
