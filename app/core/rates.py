"""Per-user rate resolution with fallback to system defaults.

Supports temporal queries (like wages) — rates can change over time.
User.custom_rates = current rates (fast access).
RateHistory = full audit trail with effective_from/effective_to.
"""

from __future__ import annotations

import datetime

from app.core.constants import OT_RATE_DIVISOR
from app.core.oncall import ONCALL_GROUP_BY_CODE
from app.core.utils import get_today

# --- System defaults ---
DEFAULT_OB_DIVISORS: dict[str, int] = {
    "OB1": 600,
    "OB2": 400,
    "OB3": 300,
    "OB4": 300,
    "OB5": 150,
}

# On-call kr/h per payslip column slot, dated per employer profile. Each entry
# holds from its date until the next one. Nexer has no fixed amounts: its rules
# carry divisors on the monthly salary instead.
_ICA_ONCALL = [
    (datetime.date(2000, 1, 1), {"oc_vardag": 75, "oc_helg": 97, "oc_helgdag": 112, "oc_storhelg": 192}),
    (datetime.date(2026, 5, 1), {"oc_vardag": 78, "oc_helg": 100, "oc_helgdag": 116, "oc_storhelg": 198}),
]
ONCALL_RATES_BY_PROFILE: dict[str, list[tuple[datetime.date, dict[str, int]]]] = {
    "ica": _ICA_ONCALL,
    "agiremus": [
        (datetime.date(2000, 1, 1), {"oc_vardag": 75, "oc_helg": 97, "oc_helgdag": 112, "oc_storhelg": 192}),
    ],
    "experis": _ICA_ONCALL,
    "nexer": [],
}


def profile_oncall_rates(profile: str, on_date: datetime.date) -> dict[str, int]:
    """Per-code on-call kr/h for a profile on a date. Unknown profiles price as ICA."""
    entries = ONCALL_RATES_BY_PROFILE.get(profile, ONCALL_RATES_BY_PROFILE["ica"])
    by_group = next((rates for start, rates in reversed(entries) if start <= on_date), {})
    return {code: by_group[group] for code, group in ONCALL_GROUP_BY_CODE.items() if group in by_group}


DEFAULT_VACATION_RATES: dict[str, float] = {
    "fixed_pct": 0.008,
    "variable_pct": 0.005,
    "payout_pct": 0.046,
}
# How the supplement is settled (a flat amount per day, and whether the variable
# part is paid per day or as a yearly lump) lives on the User, not here: these
# rates are versioned through RateHistory, and a wage revision must not open a
# new rate period carrying a payout routine with it. See User.vacation_*.

DEFAULT_OT_DIVISOR: int = OT_RATE_DIVISOR  # 72


def _resolve_rates(custom: dict, profile: str = "ica", on_date: datetime.date | None = None) -> dict:
    """Convert a raw custom_rates JSON dict into resolved rates.

    on_date picks the profile's on-call amounts. None means today's agreement, so a
    caller pricing a past period must pass a date inside that period.
    """
    if on_date is None:
        on_date = get_today()
    sick_raw = custom.get("sick") or {}
    return {
        "profile": profile,
        "ob": custom.get("ob") or {},
        "ot": custom.get("ot"),
        "oncall": {**profile_oncall_rates(profile, on_date), **(custom.get("oncall") or {})},
        "vacation": {**DEFAULT_VACATION_RATES, **(custom.get("vacation") or {})},
        "sick": {"ob_compensation": bool(sick_raw.get("ob_compensation", False))},
    }


def get_user_rates(user, session=None, effective_date: datetime.date | None = None) -> dict:
    """Resolve a user's effective rates, optionally for a specific date.

    Args:
        user: User model instance
        session: SQLAlchemy session (needed for temporal queries)
        effective_date: Date to query rates for. None = current rates from User.custom_rates
            and today's on-call agreement for the user's employer profile.

    Returns:
        {"profile": str, "ob": {}, "ot": None, "oncall": {...}, "vacation": {...}, "sick": {...}}
    """
    profile = getattr(user, "employer_profile", None) or "ica"
    if effective_date is not None and session is not None:
        from app.database.database import RateHistory

        record = (
            session.query(RateHistory)
            .filter(
                RateHistory.user_id == user.id,
                RateHistory.effective_from <= effective_date,
                # Either no end date (current rates) OR end date is on/after effective_date.
                # effective_to is set by add_new_rates() as an INCLUSIVE last valid day
                # (new_effective_from - 1 day), so this must be >= to cover that boundary day.
                (RateHistory.effective_to.is_(None)) | (RateHistory.effective_to >= effective_date),
            )
            .order_by(RateHistory.effective_from.desc())
            .first()
        )

        if record:
            return _resolve_rates(record.rates or {}, profile, effective_date)

    # Fallback: current rates from User model
    custom = getattr(user, "custom_rates", None) or {}
    return _resolve_rates(custom, profile, effective_date)


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


def update_rate_history_value(session, rate_id: int, user_id: int, rates: dict):
    """Update the rate values on an existing rate history record without touching dates.

    Only the rates JSON changes; effective_from/effective_to stay untouched so the
    timeline's integrity cannot break. Mirrors add_new_rates() for the active-record
    User.custom_rates sync.

    Raises:
        LookupError: if the record does not exist
        PermissionError: if the record belongs to another user
    """
    from sqlalchemy.orm.attributes import flag_modified

    from app.core.utils import get_today
    from app.database.database import RateHistory, User

    record = session.query(RateHistory).filter(RateHistory.id == rate_id).first()
    if not record:
        raise LookupError("Rate record not found")
    if record.user_id != user_id:
        raise PermissionError("Rate record does not belong to this user")

    record.rates = rates
    flag_modified(record, "rates")

    # Keep User.custom_rates in sync if this is the active rate record
    if record.effective_to is None and record.effective_from <= get_today():
        user = session.query(User).filter(User.id == user_id).first()
        if user:
            user.custom_rates = rates
            flag_modified(user, "custom_rates")

    session.commit()
    return record


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
    """Return all default rates for display in UI. On-call shows today's ICA amounts."""
    return {
        "ob_divisors": dict(DEFAULT_OB_DIVISORS),
        "ot_divisor": DEFAULT_OT_DIVISOR,
        "oncall": profile_oncall_rates("ica", get_today()),
        "vacation": dict(DEFAULT_VACATION_RATES),
    }
