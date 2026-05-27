"""Tests for the localhost security middleware (Host allowlist, Origin/Referer, CSRF)."""

import os
from pathlib import Path

from fastapi.testclient import TestClient

from pydbplay.app.main import (
    create_app,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_client(tmp_path: Path, base_url: str = "http://localhost") -> TestClient:
    """Isolated TestClient — NO auto-CSRF hook, so rejection tests work as-is."""
    os.environ["PYDBPLAY_APP_DIR"] = str(tmp_path)
    app = create_app()
    return TestClient(app, base_url=base_url, raise_server_exceptions=False)


def _make_disabled_client(tmp_path: Path) -> TestClient:
    """TestClient with security_enabled=False (via env var override)."""
    os.environ["PYDBPLAY_APP_DIR"] = str(tmp_path)
    os.environ["PYDBPLAY_SECURITY_ENABLED"] = "false"
    try:
        app = create_app()
        return TestClient(app, base_url="http://localhost", raise_server_exceptions=False)
    finally:
        os.environ.pop("PYDBPLAY_SECURITY_ENABLED", None)


def _csrf_token(client: TestClient) -> str:
    """Prime the client with a GET so the middleware sets the csrf_token cookie."""
    client.get("/health")
    token = client.cookies.get("csrf_token")
    assert token, "Expected csrf_token cookie after priming GET"
    return token


# ---------------------------------------------------------------------------
# 1. Host allowlist
# ---------------------------------------------------------------------------


def test_get_with_disallowed_host_is_blocked(tmp_path: Path) -> None:
    """GET with a disallowed Host header must be rejected (Host check)."""
    # base_url="http://evil.com" → TestClient sends Host: evil.com
    client = _make_client(tmp_path, base_url="http://evil.com")
    with client:
        resp = client.get("/health")
    assert resp.status_code == 400
    assert "Host not allowed" in resp.text


def test_post_with_disallowed_host_is_blocked(tmp_path: Path) -> None:
    """POST with a disallowed Host header must be rejected before CSRF check."""
    client = _make_client(tmp_path, base_url="http://evil.com")
    with client:
        resp = client.post("/api/connections/test", data={"name": "x"})
    assert resp.status_code == 400
    assert "Host not allowed" in resp.text


# ---------------------------------------------------------------------------
# 2. Origin / Referer check
# ---------------------------------------------------------------------------


def test_post_with_bad_origin_is_blocked(tmp_path: Path) -> None:
    """POST with allowed Host but Origin from evil.com must be blocked (403)."""
    client = _make_client(tmp_path, base_url="http://localhost")
    with client:
        token = _csrf_token(client)
        resp = client.post(
            "/api/connections/test",
            data={"name": "x"},
            headers={"Origin": "http://evil.com", "X-CSRFToken": token},
        )
    assert resp.status_code == 403
    assert "Cross-origin request blocked" in resp.text


def test_post_with_bad_referer_no_origin_is_blocked(tmp_path: Path) -> None:
    """POST with a bad Referer (no Origin header) must be blocked (403)."""
    client = _make_client(tmp_path, base_url="http://localhost")
    with client:
        token = _csrf_token(client)
        resp = client.post(
            "/api/connections/test",
            data={"name": "x"},
            headers={"Referer": "http://evil.com/page", "X-CSRFToken": token},
        )
    assert resp.status_code == 403
    assert "Cross-origin request blocked" in resp.text


def test_post_with_allowed_origin_passes_origin_check(tmp_path: Path) -> None:
    """POST with Origin: http://localhost is allowed (origin check passes)."""
    client = _make_client(tmp_path, base_url="http://localhost")
    with client:
        token = _csrf_token(client)
        resp = client.post(
            "/api/connections/test",
            data={"name": "x", "engine": "sqlite", "database": "/tmp/nonexistent.db"},
            headers={"Origin": "http://localhost", "X-CSRFToken": token},
        )
    # 200 (route returns ok=False for bad path, but middleware passes it through)
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# 3. CSRF double-submit token
# ---------------------------------------------------------------------------


def test_post_with_no_csrf_cookie_is_rejected(tmp_path: Path) -> None:
    """POST with no csrf_token cookie at all must return 403."""
    client = _make_client(tmp_path, base_url="http://localhost")
    with client:
        # Do NOT prime — no cookie in jar.
        resp = client.post(
            "/api/connections/test",
            data={"name": "x"},
        )
    assert resp.status_code == 403
    assert "CSRF token invalid" in resp.text


def test_post_with_mismatched_csrf_header_is_rejected(tmp_path: Path) -> None:
    """POST with a csrf_token cookie but a wrong X-CSRFToken header must return 403."""
    client = _make_client(tmp_path, base_url="http://localhost")
    with client:
        _csrf_token(client)  # prime → sets cookie
        resp = client.post(
            "/api/connections/test",
            data={"name": "x"},
            headers={"X-CSRFToken": "totally-wrong-value"},
        )
    assert resp.status_code == 403
    assert "CSRF token invalid" in resp.text


def test_post_with_valid_csrf_header_passes(tmp_path: Path) -> None:
    """POST with matching cookie + X-CSRFToken header must reach the route (200)."""
    client = _make_client(tmp_path, base_url="http://localhost")
    with client:
        token = _csrf_token(client)
        resp = client.post(
            "/api/connections/test",
            data={"name": "x", "engine": "sqlite", "database": "/tmp/nonexistent.db"},
            headers={"X-CSRFToken": token},
        )
    assert resp.status_code == 200


def test_post_with_valid_csrf_form_field_passes(tmp_path: Path) -> None:
    """POST with matching cookie + csrf_token form field (no header) must reach route."""
    client = _make_client(tmp_path, base_url="http://localhost")
    with client:
        token = _csrf_token(client)
        resp = client.post(
            "/api/connections/test",
            data={
                "name": "x",
                "engine": "sqlite",
                "database": "/tmp/nonexistent.db",
                "csrf_token": token,
            },
        )
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# 4. GET requests do NOT require CSRF token
# ---------------------------------------------------------------------------


def test_get_does_not_require_csrf_token(tmp_path: Path) -> None:
    """GET /health must return 200 even with no csrf_token cookie."""
    client = _make_client(tmp_path, base_url="http://localhost")
    with client:
        # No priming GET; no cookie in jar.
        resp = client.get("/health")
    assert resp.status_code == 200


def test_get_sets_csrf_cookie_on_response(tmp_path: Path) -> None:
    """The middleware must set the csrf_token cookie on the first GET response."""
    client = _make_client(tmp_path, base_url="http://localhost")
    with client:
        resp = client.get("/health")
    assert resp.status_code == 200
    assert "csrf_token" in client.cookies or "csrf_token" in resp.cookies


# ---------------------------------------------------------------------------
# 5. security_enabled=False bypasses all checks
# ---------------------------------------------------------------------------


def test_security_disabled_allows_disallowed_host(tmp_path: Path) -> None:
    """With security_enabled=False, disallowed Host passes through."""
    os.environ["PYDBPLAY_APP_DIR"] = str(tmp_path)
    os.environ["PYDBPLAY_SECURITY_ENABLED"] = "false"
    try:
        app = create_app()
        # Use evil.com as base_url to force Host: evil.com
        client = TestClient(app, base_url="http://evil.com", raise_server_exceptions=False)
        with client:
            resp = client.get("/health")
        assert resp.status_code == 200
    finally:
        os.environ.pop("PYDBPLAY_SECURITY_ENABLED", None)


def test_security_disabled_allows_post_without_csrf(tmp_path: Path) -> None:
    """With security_enabled=False, POST with no CSRF token passes through."""
    os.environ["PYDBPLAY_APP_DIR"] = str(tmp_path)
    os.environ["PYDBPLAY_SECURITY_ENABLED"] = "false"
    try:
        app = create_app()
        client = TestClient(app, base_url="http://localhost", raise_server_exceptions=False)
        with client:
            resp = client.post(
                "/api/connections/test",
                data={"name": "x", "engine": "sqlite", "database": "/tmp/nonexistent.db"},
            )
        assert resp.status_code == 200
    finally:
        os.environ.pop("PYDBPLAY_SECURITY_ENABLED", None)


def test_security_disabled_allows_bad_origin(tmp_path: Path) -> None:
    """With security_enabled=False, bad Origin header passes through."""
    os.environ["PYDBPLAY_APP_DIR"] = str(tmp_path)
    os.environ["PYDBPLAY_SECURITY_ENABLED"] = "false"
    try:
        app = create_app()
        client = TestClient(app, base_url="http://localhost", raise_server_exceptions=False)
        with client:
            resp = client.post(
                "/api/connections/test",
                data={"name": "x", "engine": "sqlite", "database": "/tmp/nonexistent.db"},
                headers={"Origin": "http://evil.com"},
            )
        assert resp.status_code == 200
    finally:
        os.environ.pop("PYDBPLAY_SECURITY_ENABLED", None)


# ---------------------------------------------------------------------------
# 6. Connections router tests still pass with the shared fixture (regression)
#    This is validated by running the full test suite, but we add one explicit
#    smoke test here to confirm the fixture-level auto-CSRF works.
# ---------------------------------------------------------------------------


def test_shared_client_fixture_csrf_works(client: TestClient) -> None:
    """Shared client fixture (auto-CSRF) can POST without manually handling tokens."""
    resp = client.post(
        "/api/connections/test",
        data={"name": "x", "engine": "sqlite", "database": "/tmp/nonexistent.db"},
    )
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# 7. Non-ASCII CSRF token must return 403, not 500 (regression — HIGH)
# ---------------------------------------------------------------------------


def test_post_with_non_ascii_csrf_form_field_returns_403(tmp_path: Path) -> None:
    """POST with a non-ASCII csrf_token form field must return 403, not 500.

    secrets.compare_digest raises TypeError on non-ASCII strings; the middleware
    must catch this with an isascii() pre-check before calling compare_digest.
    """
    client = _make_client(tmp_path, base_url="http://localhost")
    with client:
        _csrf_token(client)  # prime → sets a valid ASCII cookie
        resp = client.post(
            "/api/connections/test",
            # Non-ASCII value in the form field; no X-CSRFToken header
            data={"name": "x", "csrf_token": "café"},
        )
    assert resp.status_code == 403
    assert "CSRF token invalid" in resp.text
