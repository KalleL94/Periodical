"""Malformed date/time form fields must be rejected by validation, not blow up.

These routes used to call datetime.strptime on the raw form value with no guard,
so a bad value raised ValueError and the request came back as 500.
"""

import pytest


def _login(client, username, password):
    client.post("/login", data={"username": username, "password": password})


@pytest.mark.parametrize(
    "path,data",
    [
        ("/shift-override/add", {"user_id": "1", "date": "not-a-date", "shift_code": "N1"}),
        ("/oncall/add", {"user_id": "1", "date": "2026-13-45"}),
        ("/oncall/remove", {"user_id": "1", "date": ""}),
        (
            "/overtime/add",
            {"user_id": "1", "date": "15/01/2026", "start_time": "06:00", "end_time": "14:00"},
        ),
        (
            "/overtime/add",
            {"user_id": "1", "date": "2026-01-15", "start_time": "25:99", "end_time": "14:00"},
        ),
        ("/day-pay-override/set", {"user_id": "1", "date": "nope", "ob_hours_OB1": "2"}),
        ("/swaps/propose", {"target_id": "2", "requester_date": "nope", "target_date": "2026-01-16"}),
    ],
)
def test_malformed_date_returns_4xx_not_500(test_client, test_user, path, data):
    _login(test_client, "testuser", "testpass123")
    resp = test_client.post(path, data=data, follow_redirects=False)
    assert 400 <= resp.status_code < 500, f"{path} -> {resp.status_code}"
