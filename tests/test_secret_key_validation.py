import os
import subprocess
import sys


def _import_auth_with_env(secret_key: str, production: str = "true") -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["PRODUCTION"] = production
    env["SECRET_KEY"] = secret_key
    return subprocess.run(
        [sys.executable, "-c", "import app.auth.auth"],
        cwd=os.getcwd(),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_production_rejects_known_placeholder_secret():
    result = _import_auth_with_env("change-me-to-random-secret")

    assert result.returncode != 0
    assert "SECRET_KEY must be set to a strong random value in production" in result.stderr


def test_production_rejects_short_secret():
    result = _import_auth_with_env("short")

    assert result.returncode != 0
    assert "SECRET_KEY must be set to a strong random value in production" in result.stderr


def test_production_accepts_strong_secret():
    result = _import_auth_with_env("a-secure-test-secret-value-32-plus-chars")

    assert result.returncode == 0
