# app/routes/changelog.py
"""
Changelog / version history page.
"""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.auth.auth import get_current_user_optional
from app.core.news import load_releases, mark_seen
from app.database.database import get_db
from app.routes.shared import render

router = APIRouter()


@router.get("/changelog", response_class=HTMLResponse)
async def changelog_page(
    request: Request,
    db: Session = Depends(get_db),
):
    from app.core.utils import get_today

    user = await get_current_user_optional(request, db)
    # Opening the page is the acknowledgement. Recorded before rendering so the
    # nav entry is already gone from this response onwards.
    mark_seen(db, user)
    releases = load_releases()
    return render(
        "changelog.html",
        {
            "request": request,
            "user": user,
            "now": get_today(),
            "versions": releases,
            "current_version": releases[0]["version"],
            # This page IS the news, so never point at itself from its own nav
            "has_news": False,
        },
    )
