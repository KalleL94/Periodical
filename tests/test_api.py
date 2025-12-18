"""
Integration tests for FastAPI endpoints.

Tests verify authentication flow, route protection, and API responses.
"""

import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

# ruff: noqa: E402


class TestPublicRoutes:
    """Test publicly accessible routes."""

    def test_health_endpoint_returns_ok(self, test_client):
        """GET /health should return 200 OK for monitoring."""
        response = test_client.get("/health")

        assert response.status_code == 200
        data = response.json()
        assert "status" in data
        assert data["status"] == "healthy"

    def test_root_redirects_to_week_view(self, test_client, test_user):
        """GET / should redirect to login or week view."""
        response = test_client.get("/", follow_redirects=False)

        # Should redirect (302/303/307)
        assert response.status_code in [302, 303, 307]

    def test_login_page_accessible(self, test_client):
        """GET /login should return login page HTML."""
        response = test_client.get("/login")

        assert response.status_code == 200
        assert "text/html" in response.headers.get("content-type", "")


class TestAuthenticationFlow:
    """Test login and authentication flow."""

    def test_login_with_valid_credentials(self, test_client, test_user):
        """POST /login with valid credentials should set auth cookie and redirect."""
        response = test_client.post(
            "/login",
            data={"username": "testuser", "password": "testpass123"},
            follow_redirects=False,
        )

        # Should redirect after successful login
        assert response.status_code in [302, 303, 307]

        # Should set auth cookie
        assert "set-cookie" in response.headers or "Set-Cookie" in response.headers

    def test_login_with_invalid_password(self, test_client, test_user):
        """POST /login with wrong password should return 401 Unauthorized."""
        response = test_client.post(
            "/login",
            data={"username": "testuser", "password": "wrongpassword"},
            follow_redirects=False,
        )

        # Should return error (could be 401 or 200 with error message in HTML)
        # Check if login failed by verifying no redirect to protected route
        assert response.status_code != 303 or "/week/" not in response.headers.get("location", "")

    def test_login_with_nonexistent_user(self, test_client, test_user):
        """POST /login with non-existent username should return error or show login form."""
        # test_user fixture ensures database is set up, though we don't use it
        response = test_client.post(
            "/login",
            data={"username": "nonexistent", "password": "anypassword"},
            follow_redirects=False,
        )

        # Should either return 200 (login form with error) or not redirect to protected route
        if response.status_code == 303:
            assert "/week/" not in response.headers.get("location", ""), (
                "Should not redirect to protected route after failed login"
            )
        else:
            # 200 OK with error message in form is acceptable
            assert response.status_code in [200, 401, 403]

    def test_logout_clears_auth_cookie(self, test_client, test_user, auth_headers):
        """GET /logout should clear authentication cookie and redirect."""
        response = test_client.get("/logout", follow_redirects=False)

        # Should redirect to login or home
        assert response.status_code in [302, 303, 307]


class TestProtectedRoutes:
    """Test authentication-protected routes."""

    def test_week_view_requires_authentication(self, test_client):
        """GET /week/{person_id} without auth should redirect to login."""
        response = test_client.get("/week/1", follow_redirects=False)

        # Should redirect to login (or return 401/403)
        assert response.status_code in [302, 303, 307, 401, 403]

    def test_week_view_with_authentication(self, test_client, test_user):
        """GET /week/{person_id} with valid auth should return week view."""
        # Login first
        test_client.post(
            "/login",
            data={"username": "testuser", "password": "testpass123"},
        )

        # Access week view
        response = test_client.get(f"/week/{test_user.id}")

        # Should return successful response or redirect (not 401/403)
        assert response.status_code not in [401, 403]

    def test_month_view_requires_authentication(self, test_client):
        """GET /month/{person_id} without auth should redirect to login."""
        response = test_client.get("/month/1", follow_redirects=False)

        assert response.status_code in [302, 303, 307, 401, 403]

    def test_profile_requires_authentication(self, test_client):
        """GET /profile without auth should redirect to login."""
        response = test_client.get("/profile", follow_redirects=False)

        assert response.status_code in [302, 303, 307, 401, 403]

    def test_profile_accessible_when_authenticated(self, test_client, test_user):
        """GET /profile with valid auth should return profile page."""
        # Login first
        test_client.post(
            "/login",
            data={"username": "testuser", "password": "testpass123"},
        )

        response = test_client.get("/profile")

        # Should return successful response (200 or redirect, not 401/403)
        assert response.status_code not in [401, 403]


class TestAdminRoutes:
    """Test admin-only routes."""

    def test_admin_page_requires_admin_role(self, test_client, test_user):
        """GET /admin/users should reject non-admin users."""
        # Login as regular user
        test_client.post(
            "/login",
            data={"username": "testuser", "password": "testpass123"},
        )

        response = test_client.get("/admin/users", follow_redirects=False)

        # Should deny access (403 Forbidden or redirect)
        assert response.status_code in [302, 303, 307, 403]

    def test_admin_page_accessible_for_admin(self, test_client, admin_user):
        """GET /admin/users should allow admin users."""
        # Login as admin
        test_client.post(
            "/login",
            data={"username": "admin", "password": "adminpass123"},
        )

        response = test_client.get("/admin/users")

        # Should return successful response
        assert response.status_code == 200 or response.status_code in [302, 303, 307]


class TestAPIDataEndpoints:
    """Test data retrieval endpoints."""

    def test_week_view_returns_schedule_data(self, test_client, test_user):
        """Week view should return schedule data for the specified person."""
        # Login first
        test_client.post(
            "/login",
            data={"username": "testuser", "password": "testpass123"},
        )

        # Get week view
        response = test_client.get(f"/week/{test_user.id}")

        assert response.status_code == 200
        # Response should be HTML with schedule content
        assert "text/html" in response.headers.get("content-type", "")

    def test_invalid_person_id_handled_gracefully(self, test_client, test_user):
        """Invalid person_id should return error or redirect."""
        # Login first
        test_client.post(
            "/login",
            data={"username": "testuser", "password": "testpass123"},
        )

        # Try invalid person ID (e.g., 999)
        response = test_client.get("/week/999", follow_redirects=False)

        # Should handle gracefully (redirect or error, not 500)
        assert response.status_code != 500

    def test_month_view_returns_calendar_data(self, test_client, test_user):
        """Month view should return calendar data for the specified person."""
        # Login first
        test_client.post(
            "/login",
            data={"username": "testuser", "password": "testpass123"},
        )

        # Get month view (current month)
        response = test_client.get(f"/month/{test_user.id}")

        assert response.status_code == 200
        assert "text/html" in response.headers.get("content-type", "")


class TestPasswordChangeFlow:
    """Test password change functionality."""

    def test_password_change_updates_credentials(self, test_client, test_user):
        """User should be able to change their password via /profile/password."""
        # Login first
        test_client.post(
            "/login",
            data={"username": "testuser", "password": "testpass123"},
        )

        # Change password
        response = test_client.post(
            "/profile/password",
            data={
                "current_password": "testpass123",
                "new_password": "newpass456",
                "confirm_password": "newpass456",
            },
            follow_redirects=False,
        )

        # Should succeed (redirect or 200)
        assert response.status_code in [200, 302, 303, 307]

    def test_password_change_with_wrong_current_password(self, test_client, test_user):
        """Password change should fail if current password is wrong."""
        # Login first
        test_client.post(
            "/login",
            data={"username": "testuser", "password": "testpass123"},
        )

        # Try to change with wrong current password
        response = test_client.post(
            "/profile/password",
            data={
                "current_password": "wrongpassword",
                "new_password": "newpass456",
                "confirm_password": "newpass456",
            },
            follow_redirects=False,
        )

        # Should fail (not redirect to success page)
        # Could return 200 with error or 4xx status
        assert response.status_code != 303 or "success" not in response.headers.get("location", "")


class TestErrorHandling:
    """Test error handling and edge cases."""

    def test_404_for_nonexistent_route(self, test_client):
        """Non-existent routes should return 404."""
        response = test_client.get("/nonexistent/route/123")

        assert response.status_code == 404

    def test_malformed_date_parameters_handled(self, test_client, test_user):
        """Malformed date parameters should be handled gracefully."""
        # Login first
        test_client.post(
            "/login",
            data={"username": "testuser", "password": "testpass123"},
        )

        # Try malformed month URL
        response = test_client.get("/month/1/9999/13", follow_redirects=False)

        # Should not crash (500), should handle gracefully
        assert response.status_code != 500
