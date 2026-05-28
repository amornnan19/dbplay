"""Tests for Phase-5 dark mode: FOUC script and theme toggle sentinel presence."""

import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

from pydbplay.app.main import create_app

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_FOUC_SENTINEL = "localStorage.getItem('pydbplay:theme')"
_TOGGLE_SENTINEL = "$store.theme.cycle"


def _make_client(tmp_path: Path) -> TestClient:
    """Create an isolated TestClient backed by tmp_path with CSRF auto-injection."""
    import os

    os.environ["PYDBPLAY_APP_DIR"] = str(tmp_path)
    app = create_app()
    client = TestClient(app, base_url="http://localhost")

    def _inject_csrf(request):  # type: ignore[no-untyped-def]
        if request.method.upper() in {"POST", "PUT", "PATCH", "DELETE"}:
            token = client.cookies.get("csrf_token")
            if token:
                request.headers["X-CSRFToken"] = token

    client.event_hooks["request"] = [_inject_csrf]
    # Prime: a GET so the middleware sets the csrf_token cookie.
    client.get("/health")
    return client


def _make_sqlite_conn(tmp_path: Path, client: TestClient) -> int:
    """Create a SQLite connection profile and return its ID.

    Must be called inside a ``with client:`` block.
    """
    db_path = tmp_path / "test.db"
    sqlite3.connect(str(db_path)).close()
    resp = client.post(
        "/api/connections",
        data={"name": "test", "engine": "sqlite", "database": str(db_path)},
    )
    assert resp.status_code == 200, resp.text
    import re

    m = re.search(r'id="conn-(\d+)"', resp.text)
    assert m, f"Could not find conn-<id> in response: {resp.text[:300]}"
    return int(m.group(1))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_dark_mode_init_script_present_connections(tmp_path: Path) -> None:
    """GET /connections must contain the FOUC-prevention sentinel."""
    client = _make_client(tmp_path)
    with client:
        resp = client.get("/connections")
    assert resp.status_code == 200
    assert _FOUC_SENTINEL in resp.text, (
        f"FOUC script sentinel not found in /connections. Searched for: {_FOUC_SENTINEL!r}"
    )
    assert "darkMode: 'class'" in resp.text, (
        "Tailwind darkMode config line missing from /connections"
    )
    assert 'src="/static/js/theme.js"' in resp.text and "defer" in resp.text, (
        "theme.js <script defer> tag missing from /connections"
    )


def test_dark_mode_init_script_present_query_page(tmp_path: Path) -> None:
    """GET /c/{id} must contain the FOUC-prevention sentinel."""
    client = _make_client(tmp_path)
    with client:
        conn_id = _make_sqlite_conn(tmp_path, client)
        resp = client.get(f"/c/{conn_id}")
    assert resp.status_code == 200
    assert _FOUC_SENTINEL in resp.text, (
        f"FOUC script sentinel not found in /c/{conn_id}. Searched for: {_FOUC_SENTINEL!r}"
    )


def test_theme_toggle_button_present_connections(tmp_path: Path) -> None:
    """GET /connections must contain the Alpine theme toggle sentinel."""
    client = _make_client(tmp_path)
    with client:
        resp = client.get("/connections")
    assert resp.status_code == 200
    assert _TOGGLE_SENTINEL in resp.text, (
        f"Theme toggle sentinel not found in /connections. Searched for: {_TOGGLE_SENTINEL!r}"
    )


def test_theme_toggle_button_present_query_page(tmp_path: Path) -> None:
    """GET /c/{id} must contain the Alpine theme toggle sentinel."""
    client = _make_client(tmp_path)
    with client:
        conn_id = _make_sqlite_conn(tmp_path, client)
        resp = client.get(f"/c/{conn_id}")
    assert resp.status_code == 200
    assert _TOGGLE_SENTINEL in resp.text, (
        f"Theme toggle sentinel not found in /c/{conn_id}. Searched for: {_TOGGLE_SENTINEL!r}"
    )


def test_theme_static_js_served(tmp_path: Path) -> None:
    """GET /static/js/theme.js must be served and contain Alpine.store."""
    client = _make_client(tmp_path)
    with client:
        resp = client.get("/static/js/theme.js")
    assert resp.status_code == 200
    assert "Alpine.store" in resp.text, "Alpine.store not found in /static/js/theme.js"


def test_browse_page_has_theme_init(tmp_path: Path) -> None:
    """GET /c/{id}/browse/{table} must contain the FOUC-prevention sentinel."""
    import sqlite3 as _sqlite3

    client = _make_client(tmp_path)
    with client:
        # Create a DB with a real table so browse works.
        db_path = tmp_path / "browse_test.db"
        con = _sqlite3.connect(str(db_path))
        con.execute("CREATE TABLE items (id INTEGER PRIMARY KEY, name TEXT)")
        con.commit()
        con.close()
        resp = client.post(
            "/api/connections",
            data={"name": "browse-test", "engine": "sqlite", "database": str(db_path)},
        )
        assert resp.status_code == 200, resp.text
        import re

        m = re.search(r'id="conn-(\d+)"', resp.text)
        assert m, f"Could not find conn-<id> in response: {resp.text[:300]}"
        conn_id = int(m.group(1))

        resp = client.get(f"/c/{conn_id}/browse/items")
    assert resp.status_code == 200
    assert _FOUC_SENTINEL in resp.text, (
        f"FOUC script sentinel not found in /c/{conn_id}/browse/items. "
        f"Searched for: {_FOUC_SENTINEL!r}"
    )
