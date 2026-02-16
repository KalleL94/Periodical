"""Per-user rate resolution with fallback to system defaults.

Supports temporal queries (like wages) — rates can change over time.
User.custom_rates = current rates (fast access).
RateHistory = full audit trail with effective_from/effective_to.
"""

from __future__ import annotations

import datetime

from app.core.constants import OT_RATE_DIVISOR

# --- System defaults ---
DEFAULT_OB_DIVISORS: dict[str, int] = {
    "OB1": 600,
    "OB2": 400,
    "OB3": 300,
    "OB4": 300,
    "OB5": 150,
}

DEFAULT_ONCALL_RATES: dict[str, int] = {
    "OC_WEEKDAY": 75,
    "OC_WEEKEND": 97,
    "OC_WEEKEND_SAT": 97,
    "OC_WEEKEND_SUN": 97,
    "OC_WEEKEND_MON": 97,
    "OC_HOLIDAY_EVE": 97,
    "OC_HOLIDAY": 112,
    "OC_SPECIAL": 192,
}

DEFAULT_VACATION_RATES: dict[str, float] = {
    "fixed_pct": 0.008,
    "variable_pct": 0.005,
    "payout_pct": 0.046,
}

DEFAULT_OT_DIVISOR: int = OT_RATE_DIVISOR  # 72


def _resolve_rates(custom: dict) -> dict:
    """Convert a raw custom_rates JSON dict into resolved rates."""
    return {
        "ob": custom.get("ob") or {},
        "ot": custom.get("ot"),
        "oncall": {**DEFAULT_ONCALL_RATES, **(custom.get("oncall") or {})},
        "vacation": {**DEFAULT_VACATION_RATES, **(custom.get("vacation") or {})},
    }


def get_user_rates(user, session=None, effective_date: datetime.date | None = None) -> dict:
    """Resolve a user's effective rates, optionally for a specific date.

    Args:
        user: User model instance
        session: SQLAlchemy session (needed for temporal queries)
        effective_date: Date to query rates for. None = current rates from User.custom_rates.

    Returns:
        {"ob": {}, "ot": None, "oncall": {...}, "vacation": {...}}
    """
    if effective_date is not None and session is not None:
        from app.database.database import RateHistory

        record = (
            session.query(RateHistory)
            .filter(
                RateHistory.user_id == user.id,
                RateHistory.effective_from <= effective_date,
                (RateHistory.effective_to.is_(None)) | (RateHistory.effective_to > effective_date),
            )
            .order_by(RateHistory.effective_from.desc())
            .first()
        )

        if record:
            return _resolve_rates(record.rates or {})

    # Fallback: current rates from User model
    custom = getattr(user, "custom_rates", None) or {}
    return _resolve_rates(custom)


def add_new_rates(session, user_id: int, rates: dict, effective_from: datetime.date, created_by: int | None = None):
    """Add new rate entry, closing previous one. Mirrors add_new_wage() pattern."""
    from sqlalchemy.orm.attributes import flag_modified

    from app.core.utils import get_today
    from app.database.database import RateHistory, User

    # Seed: if no RateHistory exists yet but user has custom_rates, create initial entry
    has_any = session.query(RateHistory).filter(RateHistory.user_id == user_id).count()
    if not has_any:
        user = session.query(User).filter(User.id == user_id).first()
        existing = getattr(user, "custom_rates", None) or {}
        if existing:
            seed_from = getattr(user, "employment_start_date", None) or datetime.date(2020, 1, 1)
            seed = RateHistory(
                user_id=user_id,
                rates=existing,
                effective_from=seed_from,
                effective_to=None,
                created_by=created_by,
            )
            session.add(seed)
            session.flush()

    # Close previous rate history
    previous = (
        session.query(RateHistory).filter(RateHistory.user_id == user_id, RateHistory.effective_to.is_(None)).first()
    )
    if previous:
        previous.effective_to = effective_from - datetime.timedelta(days=1)

    # Create new entry
    new_record = RateHistory(
        user_id=user_id,
        rates=rates,
        effective_from=effective_from,
        effective_to=None,
        created_by=created_by,
    )
    session.add(new_record)

    # Update User.custom_rates if effective_from <= today
    if effective_from <= get_today():
        user = session.query(User).filter(User.id == user_id).first()
        if user:
            user.custom_rates = rates
            flag_modified(user, "custom_rates")

    session.commit()
    return new_record


def get_rate_history(session, user_id: int) -> list[dict]:
    """Get all rate history for a user, newest first."""
    from app.database.database import RateHistory

    records = (
        session.query(RateHistory)
        .filter(RateHistory.user_id == user_id)
        .order_by(RateHistory.effective_from.desc())
        .all()
    )

    from app.core.utils import get_today

    today = get_today()

    return [
        {
            "id": r.id,
            "rates": r.rates or {},
            "effective_from": r.effective_from,
            "effective_to": r.effective_to,
            "status": "future"
            if r.effective_from > today
            else ("current" if r.effective_to is None or r.effective_to >= today else "historical"),
            "created_at": r.created_at,
        }
        for r in records
    ]


def delete_rate_history(session, rate_id: int, user_id: int):
    """Delete a rate history record. Reopen previous if it was closed by this one."""
    from app.database.database import RateHistory

    record = session.query(RateHistory).filter(RateHistory.id == rate_id, RateHistory.user_id == user_id).first()
    if not record:
        return

    deleted_from = record.effective_from
    session.delete(record)
    session.flush()

    # Reopen the record whose effective_to was set because of the deleted entry
    prev = (
        session.query(RateHistory)
        .filter(
            RateHistory.user_id == user_id,
            RateHistory.effective_to == deleted_from - datetime.timedelta(days=1),
        )
        .first()
    )
    if prev:
        # Check if there's still a later record — if so, don't reopen
        later = (
            session.query(RateHistory)
            .filter(
                RateHistory.user_id == user_id,
                RateHistory.effective_from > prev.effective_from,
            )
            .first()
        )
        if not later:
            prev.effective_to = None

    session.commit()


def get_all_defaults() -> dict:
    """Return all default rates for display in UI."""
    return {
        "ob_divisors": dict(DEFAULT_OB_DIVISORS),
        "ot_divisor": DEFAULT_OT_DIVISOR,
        "oncall": dict(DEFAULT_ONCALL_RATES),
        "vacation": dict(DEFAULT_VACATION_RATES),
    }
