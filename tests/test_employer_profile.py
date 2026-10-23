"""Employer profile on User: default, admin dropdown, Excel export for everyone."""


def _login(client, username, password):
    client.post("/login", data={"username": username, "password": password})


def test_new_user_defaults_to_ica(test_db, test_user):
    assert test_user.employer_profile == "ica"


def test_admin_sets_profile(test_client, admin_user, test_user, test_db):
    _login(test_client, "admin", "adminpass123")
    r = test_client.post(
        f"/admin/users/{test_user.id}/set-employer-profile",
        data={"employer_profile": "nexer"},
        follow_redirects=False,
    )
    assert r.status_code == 302
    test_db.refresh(test_user)
    assert test_user.employer_profile == "nexer"


def test_admin_rejects_unknown_profile(test_client, admin_user, test_user, test_db):
    _login(test_client, "admin", "adminpass123")
    r = test_client.post(
        f"/admin/users/{test_user.id}/set-employer-profile",
        data={"employer_profile": "foo"},
        follow_redirects=False,
    )
    assert r.status_code == 400
    test_db.refresh(test_user)
    assert test_user.employer_profile == "ica"


def test_edit_page_shows_profile_dropdown(test_client, admin_user, test_user):
    _login(test_client, "admin", "adminpass123")
    r = test_client.get(f"/admin/users/{test_user.id}")
    assert r.status_code == 200
    assert "set-employer-profile" in r.text


def test_excel_button_for_everyone(test_client, test_user):
    _login(test_client, "testuser", "testpass123")
    r = test_client.get(f"/month/{test_user.id}?year=2026&month=6")
    assert r.status_code == 200
    assert "export-excel" in r.text
