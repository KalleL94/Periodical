"""Token-authenticated ICS subscription feed.

The token travels in the URL (calendar clients cannot send headers), so it
is a dedicated low-privilege token: a leaked feed URL exposes the schedule
only, never the API. Lookups go through the SHA-256 hash, mirroring API
key authentication.
"""

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session

from app.auth.auth import hash_api_key
from app.core.calendar_export import feed_window, generate_ical_for_user
from app.core.utils import get_today
from app.database.database import User, get_db

router = APIRouter()


@router.get("/calendar/feed/{token}/schema.ics", name="calendar_feed")
async def calendar_feed(token: str, db: Session = Depends(get_db)) -> Response:
    """Serves the user's actual schedule as a pollable iCal feed."""
    user = db.query(User).filter(User.calendar_token == hash_api_key(token)).first()
    if user is None:
        # 404 (not 401) so the endpoint does not confirm token existence
        raise HTTPException(status_code=404)

    start_date, end_date = feed_window(get_today())
    ical_content = generate_ical_for_user(user, start_date, end_date, lang=user.language, session=db, as_feed=True)

    return Response(
        content=ical_content,
        media_type="text/calendar; charset=utf-8",
    )
