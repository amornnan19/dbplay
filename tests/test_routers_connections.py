"""Tests for /api/connections endpoints (HTMX HTML-partial responses)."""

import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

from pydbplay.app.main import create_app

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

HTMX_HEADERS = {"HX-Request": "true"}


def _make_client(tmp_path: Path) -> TestClient:
    """Create an isolated TestClient backed by tmp_path.

    Uses base_url="http://localhost" so the Host header passes the security
    middleware's allowlist (default is "testserver" which would 400).

    An httpx request hook copies the csrf_token cookie into the X-CSRFToken
    header for every unsafe method, matching what the browser HTMX extension
    does in production and satisfying the CSRF double-submit check.
    """
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


def _seed_sqlite(path: Path) -> None:
    """Create a minimal SQLite DB at *path* so test_connection returns ok."""
    with sqlite3.connect(path) as cx:
        cx.execute("CREATE TABLE IF NOT EXISTS sample (id INTEGER PRIMARY KEY, name TEXT)")
        cx.commit()


# ---------------------------------------------------------------------------
# GET /api/connections — empty list
# ---------------------------------------------------------------------------


def test_list_connections_empty(tmp_path: Path) -> None:
    """GET /api/connections returns 200 with empty-state message when no profiles exist."""
    client = _make_client(tmp_path)
    with client:
        resp = client.get("/api/connections")
    assert resp.status_code == 200
    assert "No connections" in resp.text


# ---------------------------------------------------------------------------
# GET /connections page
# ---------------------------------------------------------------------------


def test_connections_page_renders(tmp_path: Path) -> None:
    """GET /connections renders 200 with the page shell."""
    client = _make_client(tmp_path)
    with client:
        resp = client.get("/connections")
    assert resp.status_code == 200
    assert "pydbplay" in resp.text


# ---------------------------------------------------------------------------
# POST /api/connections — create
# ---------------------------------------------------------------------------


def test_create_connection_appears_in_list(tmp_path: Path) -> None:
    """POST create returns 200 and the new connection name is visible in the list HTML."""
    client = _make_client(tmp_path)
    db_path = tmp_path / "mydb.sqlite"
    _seed_sqlite(db_path)

    with client:
        resp = client.post(
            "/api/connections",
            data={
                "name": "My SQLite",
                "engine": "sqlite",
                "database": str(db_path),
            },
        )
    assert resp.status_code == 200
    assert "My SQLite" in resp.text


def test_create_multiple_connections(tmp_path: Path) -> None:
    """Creating two connections both appear in the list."""
    client = _make_client(tmp_path)
    db1 = tmp_path / "db1.sqlite"
    db2 = tmp_path / "db2.sqlite"
    _seed_sqlite(db1)
    _seed_sqlite(db2)

    with client:
        client.post(
            "/api/connections",
            data={"name": "First DB", "engine": "sqlite", "database": str(db1)},
        )
        resp = client.post(
            "/api/connections",
            data={"name": "Second DB", "engine": "sqlite", "database": str(db2)},
        )
    assert resp.status_code == 200
    assert "First DB" in resp.text
    assert "Second DB" in resp.text


# ---------------------------------------------------------------------------
# POST /api/connections/test
# ---------------------------------------------------------------------------


def test_test_connection_valid_sqlite(tmp_path: Path) -> None:
    """POST /api/connections/test with a valid sqlite path returns ok partial."""
    client = _make_client(tmp_path)
    db_path = tmp_path / "target.sqlite"
    _seed_sqlite(db_path)

    with client:
        resp = client.post(
            "/api/connections/test",
            data={"name": "x", "engine": "sqlite", "database": str(db_path)},
        )
    assert resp.status_code == 200
    # ok=True renders a green success partial
    assert "Success" in resp.text or "successful" in resp.text.lower()


def test_test_connection_invalid_sqlite_path(tmp_path: Path) -> None:
    """POST /api/connections/test with a path in a non-existent dir returns fail partial."""
    client = _make_client(tmp_path)
    bad_path = str(tmp_path / "no_such_dir" / "missing.db")

    with client:
        resp = client.post(
            "/api/connections/test",
            data={"name": "x", "engine": "sqlite", "database": bad_path},
        )
    assert resp.status_code == 200
    assert "Failed" in resp.text or "fail" in resp.text.lower() or "False" in resp.text


def test_test_connection_mysql_fails_on_bad_host(tmp_path: Path) -> None:
    """POST /api/connections/test for mysql returns a fail when host is unreachable.

    MySQL is now a supported engine; test_connection() is attempted and fails
    with a connection error (not an "unsupported engine" message).
    """
    client = _make_client(tmp_path)

    with client:
        resp = client.post(
            "/api/connections/test",
            data={
                "name": "x",
                "engine": "mysql",
                "host": "127.0.0.1",
                "port": "19999",  # nothing listening here
                "database": "mydb",
                "username": "admin",
            },
        )
    assert resp.status_code == 200
    assert "Failed" in resp.text or "fail" in resp.text.lower()


# ---------------------------------------------------------------------------
# PATCH /api/connections/{id}
# ---------------------------------------------------------------------------


def test_update_connection_reflected_in_list(tmp_path: Path) -> None:
    """PATCH update changes the connection name and the new name is in the list."""
    client = _make_client(tmp_path)
    db_path = tmp_path / "db.sqlite"
    _seed_sqlite(db_path)

    with client:
        # Create
        resp = client.post(
            "/api/connections",
            data={"name": "OldName", "engine": "sqlite", "database": str(db_path)},
        )
        assert resp.status_code == 200
        assert "OldName" in resp.text

        # Find the id from the list
        list_resp = client.get("/api/connections")
        assert "OldName" in list_resp.text

        # Get the id — use the repository directly via the app
        from pydbplay.db.repository import Repository

        repo: Repository = client.app.state.repository
        conns = repo.list_connections()
        conn_id = conns[0].id

        # Patch
        patch_resp = client.patch(
            f"/api/connections/{conn_id}",
            data={"name": "NewName"},
        )
    assert patch_resp.status_code == 200
    assert "NewName" in patch_resp.text


def test_update_connection_not_found(tmp_path: Path) -> None:
    """PATCH on a non-existent id returns 404 error partial."""
    client = _make_client(tmp_path)
    with client:
        resp = client.patch("/api/connections/9999", data={"name": "X"})
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# DELETE /api/connections/{id}
# ---------------------------------------------------------------------------


def test_delete_connection_returns_204(tmp_path: Path) -> None:
    """DELETE returns 204 and the connection is gone from the list."""
    client = _make_client(tmp_path)
    db_path = tmp_path / "db.sqlite"
    _seed_sqlite(db_path)

    with client:
        # Create
        client.post(
            "/api/connections",
            data={"name": "ToDelete", "engine": "sqlite", "database": str(db_path)},
        )

        from pydbplay.db.repository import Repository

        repo: Repository = client.app.state.repository
        conns = repo.list_connections()
        conn_id = conns[0].id

        # Delete
        del_resp = client.delete(f"/api/connections/{conn_id}")
        assert del_resp.status_code == 204

        # Verify it's gone
        list_resp = client.get("/api/connections")
        assert "ToDelete" not in list_resp.text


def test_delete_nonexistent_connection(tmp_path: Path) -> None:
    """DELETE on a missing id is a no-op (delete_connection returns False, still 204)."""
    client = _make_client(tmp_path)
    with client:
        resp = client.delete("/api/connections/9999")
    # Spec says 204; disconnect is a no-op, delete_connection returns False but we still 204
    assert resp.status_code == 204


# ---------------------------------------------------------------------------
# POST /api/connections/{id}/connect
# ---------------------------------------------------------------------------


def test_connect_sqlite_sets_last_used(tmp_path: Path) -> None:
    """POST /connect on a valid sqlite connection returns 200 with HX-Redirect and sets last_used_at."""
    client = _make_client(tmp_path)
    db_path = tmp_path / "target.sqlite"
    _seed_sqlite(db_path)

    with client:
        client.post(
            "/api/connections",
            data={"name": "ConnTest", "engine": "sqlite", "database": str(db_path)},
        )

        from pydbplay.db.repository import Repository

        repo: Repository = client.app.state.repository
        conn_id = repo.list_connections()[0].id

        resp = client.post(f"/api/connections/{conn_id}/connect")
        assert resp.status_code == 200
        assert resp.headers.get("HX-Redirect") == f"/c/{conn_id}"

        # last_used_at must be set now
        updated = repo.get_connection(conn_id)
        assert updated is not None
        assert updated.last_used_at is not None


def test_connect_missing_profile_returns_404(tmp_path: Path) -> None:
    """POST /connect for a non-existent id returns 404 error partial."""
    client = _make_client(tmp_path)
    with client:
        resp = client.post("/api/connections/9999/connect")
    assert resp.status_code == 404


def test_connect_mysql_builds_adapter_and_redirects(tmp_path: Path) -> None:
    """POST /connect for a mysql profile returns 200 with HX-Redirect.

    MySQL is now a supported engine.  The adapter is built lazily (no real DB
    connection until the first query), so /connect succeeds and returns an
    HX-Redirect header pointing to the workspace.
    """
    client = _make_client(tmp_path)

    with client:
        client.post(
            "/api/connections",
            data={
                "name": "MySQL Conn",
                "engine": "mysql",
                "database": "mydb",
                "host": "localhost",
                "port": "3306",
                "username": "admin",
            },
        )

        from pydbplay.db.repository import Repository

        repo: Repository = client.app.state.repository
        conn_id = repo.list_connections()[0].id

        resp = client.post(f"/api/connections/{conn_id}/connect")
    assert resp.status_code == 200
    assert resp.headers.get("HX-Redirect") == f"/c/{conn_id}"


# ---------------------------------------------------------------------------
# PATCH partial-update — field preservation
# ---------------------------------------------------------------------------


def test_patch_preserves_other_fields(tmp_path: Path) -> None:
    """PATCH with only name changed leaves all other fields intact."""
    client = _make_client(tmp_path)
    db_path = tmp_path / "preserve.sqlite"
    _seed_sqlite(db_path)

    with client:
        # Create with ALL fields populated
        client.post(
            "/api/connections",
            data={
                "name": "OrigName",
                "engine": "sqlite",
                "host": "localhost",
                "port": "5432",
                "database": str(db_path),
                "username": "dbuser",
                "read_only": "true",
                "color": "#3366cc",
            },
        )

        from pydbplay.db.repository import Repository

        repo: Repository = client.app.state.repository
        conn_id = repo.list_connections()[0].id

        # PATCH only name
        patch_resp = client.patch(
            f"/api/connections/{conn_id}",
            data={"name": "NewName"},
        )
        assert patch_resp.status_code == 200

        # Verify via repository that all other fields are unchanged
        updated = repo.get_connection(conn_id)
        assert updated is not None
        assert updated.name == "NewName"
        assert updated.host == "localhost"
        assert updated.port == 5432
        assert updated.username == "dbuser"
        assert updated.database == str(db_path)
        assert updated.read_only is True
        assert updated.color == "#3366cc"


# ---------------------------------------------------------------------------
# XSS escaping in connection list
# ---------------------------------------------------------------------------


def test_connection_name_xss_is_escaped_in_list(tmp_path: Path) -> None:
    """POST a connection with an XSS name; GET list must HTML-escape it."""
    client = _make_client(tmp_path)
    db_path = tmp_path / "xss.sqlite"
    _seed_sqlite(db_path)

    xss_name = "<script>alert(1)</script>"

    with client:
        client.post(
            "/api/connections",
            data={"name": xss_name, "engine": "sqlite", "database": str(db_path)},
        )
        list_resp = client.get("/api/connections")

    assert list_resp.status_code == 200
    assert "&lt;script&gt;" in list_resp.text
    assert "<script>alert(1)</script>" not in list_resp.text
