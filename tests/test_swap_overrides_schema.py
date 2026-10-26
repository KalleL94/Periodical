import datetime

from app.core.schedule.period import _batch_fetch_swap_map
from app.database.database import ShiftSwap, SwapStatus


def _swap(requester, target, d1, d2, applied=None):
    kwargs = {} if applied is None else {"applied_as_override": applied}
    return ShiftSwap(
        requester_id=requester.id,
        target_id=target.id,
        requester_date=d1,
        target_date=d2,
        requester_shift_code="N1",
        target_shift_code="N2",
        status=SwapStatus.ACCEPTED,
        **kwargs,
    )


def test_new_columns_default_legacy(test_db, test_user, admin_user):
    swap = _swap(test_user, admin_user, datetime.date(2030, 1, 7), datetime.date(2030, 1, 8))
    test_db.add(swap)
    test_db.commit()
    test_db.refresh(swap)
    assert swap.applied_as_override is False


def test_swap_map_ignores_overridden_swaps(test_db, test_user, admin_user):
    legacy_day = datetime.date(2030, 1, 7)
    new_day = datetime.date(2030, 2, 4)
    test_db.add(_swap(test_user, admin_user, legacy_day, legacy_day))
    test_db.add(_swap(test_user, admin_user, new_day, new_day, applied=True))
    test_db.commit()

    result = _batch_fetch_swap_map(
        test_db,
        [test_user.person_id, admin_user.person_id],
        datetime.date(2030, 1, 1),
        datetime.date(2030, 2, 28),
        rotation_to_user_id={test_user.person_id: test_user.id, admin_user.person_id: admin_user.id},
    )
    dates = {d for (_, d) in result}
    assert legacy_day in dates
    assert new_day not in dates
