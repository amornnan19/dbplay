"""Tests for /api/c/{conn_id}/query* endpoints and /c/{conn_id} workspace pages."""

import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

from pydbplay.app.main import create_app

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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
    client.get("/health")
    return client


def _seed_target_db(db_path: Path, rows: int = 5) -> None:
    """Create a target SQLite DB with a 'things' table seeded with *rows* rows."""
    with sqlite3.connect(db_path) as cx:
        cx.execute(
            "CREATE TABLE IF NOT EXISTS things (id INTEGER PRIMARY KEY, label TEXT NOT NULL)"
        )
        for i in range(1, rows + 1):
            cx.execute("INSERT INTO things (label) VALUES (?)", (f"thing_{i}",))
        cx.commit()


def _register_connection(client: TestClient, db_path: Path, *, read_only: bool = False) -> int:
    """Create a connection profile via POST and return its id."""
    data: dict[str, str] = {
        "name": "Test SQLite",
        "engine": "sqlite",
        "database": str(db_path),
    }
    if read_only:
        data["read_only"] = "on"
    client.post("/api/connections", data=data)

    from pydbplay.db.repository import Repository

    repo: Repository = client.app.state.repository  # type: ignore[attr-defined]
    return repo.list_connections()[0].id


# ---------------------------------------------------------------------------
# POST /api/c/{conn_id}/query — basic SELECT
# ---------------------------------------------------------------------------


def test_run_query_returns_result_grid(tmp_path: Path) -> None:
    """POST query with SELECT * returns 200, column names, and a data value in the grid HTML."""
    db_path = tmp_path / "target.db"
    _seed_target_db(db_path, rows=3)
    client = _make_client(tmp_path)

    with client:
        conn_id = _register_connection(client, db_path)
        resp = client.post(
            f"/api/c/{conn_id}/query",
            data={"sql": "SELECT * FROM things"},
        )

    assert resp.status_code == 200
    assert "id" in resp.text
    assert "label" in resp.text
    assert "thing_1" in resp.text
    assert "3 rows" in resp.text or "3 row" in resp.text


# ---------------------------------------------------------------------------
# auto-limit: SELECT with limit=2 → only 2 rows + limited notice
# ---------------------------------------------------------------------------


def test_run_query_auto_limit(tmp_path: Path) -> None:
    """POST query with limit=2 returns only 2 rows and the 'limited' notice."""
    db_path = tmp_path / "target.db"
    _seed_target_db(db_path, rows=5)
    client = _make_client(tmp_path)

    with client:
        conn_id = _register_connection(client, db_path)
        resp = client.post(
            f"/api/c/{conn_id}/query",
            data={"sql": "SELECT * FROM things", "limit": "2"},
        )

    assert resp.status_code == 200
    assert "2 rows" in resp.text or "2 row" in resp.text
    # The "limited" notice must appear
    assert "limited" in resp.text.lower() or "limit" in resp.text.lower()


# ---------------------------------------------------------------------------
# bad SQL → 200 with error state (not 500)
# ---------------------------------------------------------------------------


def test_run_query_bad_sql_returns_error_partial(tmp_path: Path) -> None:
    """POST with a bad table name returns 200 with an error message, not 500."""
    db_path = tmp_path / "target.db"
    _seed_target_db(db_path)
    client = _make_client(tmp_path)

    with client:
        conn_id = _register_connection(client, db_path)
        resp = client.post(
            f"/api/c/{conn_id}/query",
            data={"sql": "SELECT * FROM nonexistent_table_xyz"},
        )

    assert resp.status_code == 200
    # Error message must be in the HTML
    assert "error" in resp.text.lower() or "nonexistent_table_xyz" in resp.text


# ---------------------------------------------------------------------------
# read-only connection + UPDATE → error state (not 500)
# ---------------------------------------------------------------------------


def test_run_query_read_only_update_error(tmp_path: Path) -> None:
    """POST UPDATE on a read-only connection returns 200 error state, not 500."""
    db_path = tmp_path / "target.db"
    _seed_target_db(db_path)
    client = _make_client(tmp_path)

    with client:
        conn_id = _register_connection(client, db_path, read_only=True)
        resp = client.post(
            f"/api/c/{conn_id}/query",
            data={"sql": "UPDATE things SET label = 'x' WHERE id = 1"},
        )

    assert resp.status_code == 200
    assert "error" in resp.text.lower()


# ---------------------------------------------------------------------------
# POST /api/c/{conn_id}/query/validate
# ---------------------------------------------------------------------------


def test_validate_destructive_sql(tmp_path: Path) -> None:
    """POST validate with DELETE returns is_destructive indicator."""
    db_path = tmp_path / "target.db"
    _seed_target_db(db_path)
    client = _make_client(tmp_path)

    with client:
        conn_id = _register_connection(client, db_path)
        resp = client.post(
            f"/api/c/{conn_id}/query/validate",
            data={"sql": "DELETE FROM things WHERE id = 1"},
        )

    assert resp.status_code == 200
    # The validation partial shows the destructive badge
    assert "destructive" in resp.text.lower()


def test_validate_malformed_sql(tmp_path: Path) -> None:
    """POST validate with malformed SQL shows parse error."""
    db_path = tmp_path / "target.db"
    _seed_target_db(db_path)
    client = _make_client(tmp_path)

    with client:
        conn_id = _register_connection(client, db_path)
        # "SELECT (((" has unclosed parens — sqlglot raises ParseError for this
        resp = client.post(
            f"/api/c/{conn_id}/query/validate",
            data={"sql": "SELECT ((("},
        )

    assert resp.status_code == 200
    assert (
        "error" in resp.text.lower()
        or "parse" in resp.text.lower()
        or "invalid" in resp.text.lower()
    )


def test_validate_good_select(tmp_path: Path) -> None:
    """POST validate with a valid SELECT returns ok / valid indicator."""
    db_path = tmp_path / "target.db"
    _seed_target_db(db_path)
    client = _make_client(tmp_path)

    with client:
        conn_id = _register_connection(client, db_path)
        resp = client.post(
            f"/api/c/{conn_id}/query/validate",
            data={"sql": "SELECT * FROM things"},
        )

    assert resp.status_code == 200
    assert "valid" in resp.text.lower()


# ---------------------------------------------------------------------------
# GET /api/c/{conn_id}/query/history
# ---------------------------------------------------------------------------


def test_query_history_after_run(tmp_path: Path) -> None:
    """GET history after running a query contains the executed SQL."""
    db_path = tmp_path / "target.db"
    _seed_target_db(db_path)
    client = _make_client(tmp_path)

    with client:
        conn_id = _register_connection(client, db_path)
        # Run a query first
        client.post(
            f"/api/c/{conn_id}/query",
            data={"sql": "SELECT * FROM things"},
        )
        # Then fetch history
        resp = client.get(f"/api/c/{conn_id}/query/history")

    assert resp.status_code == 200
    assert "SELECT * FROM things" in resp.text


def test_query_history_empty(tmp_path: Path) -> None:
    """GET history with no prior queries returns empty state message."""
    db_path = tmp_path / "target.db"
    _seed_target_db(db_path)
    client = _make_client(tmp_path)

    with client:
        conn_id = _register_connection(client, db_path)
        resp = client.get(f"/api/c/{conn_id}/query/history")

    assert resp.status_code == 200
    assert "no queries" in resp.text.lower()


# ---------------------------------------------------------------------------
# GET /c/{conn_id} and GET /c/{conn_id}/query — workspace pages
# ---------------------------------------------------------------------------


def test_workspace_page_renders(tmp_path: Path) -> None:
    """GET /c/{conn_id} returns 200 with the SQL editor and Run button."""
    db_path = tmp_path / "target.db"
    _seed_target_db(db_path)
    client = _make_client(tmp_path)

    with client:
        conn_id = _register_connection(client, db_path)
        resp = client.get(f"/c/{conn_id}")

    assert resp.status_code == 200
    assert "sql-editor" in resp.text
    assert "Run" in resp.text
    assert "Test SQLite" in resp.text


def test_workspace_query_page_renders(tmp_path: Path) -> None:
    """GET /c/{conn_id}/query returns 200 with the SQL editor."""
    db_path = tmp_path / "target.db"
    _seed_target_db(db_path)
    client = _make_client(tmp_path)

    with client:
        conn_id = _register_connection(client, db_path)
        resp = client.get(f"/c/{conn_id}/query")

    assert resp.status_code == 200
    assert "sql-editor" in resp.text
    assert "Run" in resp.text


def test_workspace_page_missing_conn_returns_404(tmp_path: Path) -> None:
    """GET /c/9999 returns 404 when the connection profile does not exist."""
    client = _make_client(tmp_path)

    with client:
        resp = client.get("/c/9999")

    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Missing-connection 404 on POST query endpoints (HIGH)
# ---------------------------------------------------------------------------


def test_run_query_missing_conn_returns_404(tmp_path: Path) -> None:
    """POST /api/c/9999/query returns 404 (not 500) when connection doesn't exist."""
    client = _make_client(tmp_path)

    with client:
        resp = client.post("/api/c/9999/query", data={"sql": "SELECT 1"})

    assert resp.status_code == 404


def test_validate_query_missing_conn_returns_404(tmp_path: Path) -> None:
    """POST /api/c/9999/query/validate returns 404 (not 500) when connection doesn't exist."""
    client = _make_client(tmp_path)

    with client:
        resp = client.post("/api/c/9999/query/validate", data={"sql": "SELECT 1"})

    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Adversarial XSS: SQL containing <script> is autoescaped in history (LOW)
# ---------------------------------------------------------------------------


def test_history_xss_sql_is_escaped(tmp_path: Path) -> None:
    """History endpoint autoescapes <script> tags; raw tag must NOT appear in HTML."""
    db_path = tmp_path / "target.db"
    _seed_target_db(db_path)
    client = _make_client(tmp_path)
    xss_sql = "SELECT '<script>alert(1)</script>'"

    with client:
        conn_id = _register_connection(client, db_path)
        # Execute the query so it lands in history.
        client.post(f"/api/c/{conn_id}/query", data={"sql": xss_sql})
        # Fetch history.
        resp = client.get(f"/api/c/{conn_id}/query/history")

    assert resp.status_code == 200
    assert "&lt;script&gt;" in resp.text, "Escaped form must be present"
    assert "<script>alert(1)</script>" not in resp.text, "Raw <script> must NOT appear"


# ---------------------------------------------------------------------------
# limit clamp: limit=0 and limit=-1 are rejected with 422
# ---------------------------------------------------------------------------


def test_run_query_invalid_limit_rejected(tmp_path: Path) -> None:
    """POST /api/c/{conn_id}/query with limit=0 or limit=-1 returns 422."""
    db_path = tmp_path / "target.db"
    _seed_target_db(db_path)
    client = _make_client(tmp_path)

    with client:
        conn_id = _register_connection(client, db_path)
        resp_zero = client.post(
            f"/api/c/{conn_id}/query",
            data={"sql": "SELECT * FROM things", "limit": "0"},
        )
        resp_neg = client.post(
            f"/api/c/{conn_id}/query",
            data={"sql": "SELECT * FROM things", "limit": "-1"},
        )

    assert resp_zero.status_code == 422
    assert resp_neg.status_code == 422


# ---------------------------------------------------------------------------
# POST /api/connections/{id}/connect → HX-Redirect
# ---------------------------------------------------------------------------


def test_connect_returns_hx_redirect(tmp_path: Path) -> None:
    """POST /api/connections/{id}/connect returns 200 with HX-Redirect: /c/{id}."""
    db_path = tmp_path / "target.db"
    _seed_target_db(db_path)
    client = _make_client(tmp_path)

    with client:
        conn_id = _register_connection(client, db_path)
        resp = client.post(f"/api/connections/{conn_id}/connect")

    assert resp.status_code == 200
    assert resp.headers.get("HX-Redirect") == f"/c/{conn_id}"


def test_run_query_sets_hx_trigger_for_history_refresh(tmp_path: Path) -> None:
    """POST query sets HX-Trigger: query-ran so the history panel auto-refreshes.

    The query is recorded in history on BOTH success and failure, so the header
    must be present on both paths.
    """
    db_path = tmp_path / "target.db"
    _seed_target_db(db_path, rows=3)
    client = _make_client(tmp_path)

    with client:
        conn_id = _register_connection(client, db_path)
        ok = client.post(f"/api/c/{conn_id}/query", data={"sql": "SELECT * FROM things"})
        assert ok.status_code == 200
        assert ok.headers.get("HX-Trigger") == "query-ran"

        bad = client.post(f"/api/c/{conn_id}/query", data={"sql": "SELECT * FROM missing_table"})
        assert bad.status_code == 200
        assert bad.headers.get("HX-Trigger") == "query-ran"


# ---------------------------------------------------------------------------
# Phase 4: workspace pages embed __pydbplayProfiles
# ---------------------------------------------------------------------------


def test_workspace_page_embeds_profiles(tmp_path: Path) -> None:
    """GET /c/{conn_id} embeds window.__pydbplayProfiles in the page HTML.

    Asserts that:
    - The profiles bootstrap variable is present.
    - The profile name and active conn_id are embedded.
    - No sensitive fields (password_encrypted / password) leak into the HTML.
    """
    db_path = tmp_path / "target.db"
    _seed_target_db(db_path)
    client = _make_client(tmp_path)

    with client:
        conn_id = _register_connection(client, db_path)
        resp = client.get(f"/c/{conn_id}")

    assert resp.status_code == 200
    assert "window.__pydbplayProfiles" in resp.text
    # Profile name must appear in the embedded JSON
    assert '"Test SQLite"' in resp.text
    # Active connection id must be embedded
    assert f"window.__pydbplayActiveConnId = {conn_id}" in resp.text
    # Sensitive fields must NOT appear in the page
    assert "password_encrypted" not in resp.text
    assert "password" not in resp.text


# ---------------------------------------------------------------------------
# Phase 5: EXPLAIN viewer — badge rendered in result_grid partial
# ---------------------------------------------------------------------------


def test_run_explain_returns_explain_badge(tmp_path: Path) -> None:
    """POST EXPLAIN SELECT 1 on a SQLite connection returns the EXPLAIN badge in HTML."""
    db_path = tmp_path / "target.db"
    _seed_target_db(db_path)
    client = _make_client(tmp_path)

    with client:
        conn_id = _register_connection(client, db_path)
        resp = client.post(
            f"/api/c/{conn_id}/query",
            data={"sql": "EXPLAIN SELECT 1"},
        )

    assert resp.status_code == 200
    # The indigo EXPLAIN badge must appear in the rendered HTML
    assert "EXPLAIN</span>" in resp.text or "EXPLAIN" in resp.text
    # The pre block (monospace plan) or grid must be present
    assert "<pre" in resp.text or "<table" in resp.text


def test_browse_page_embeds_profiles(tmp_path: Path) -> None:
    """GET /c/{conn_id}/browse/{table} embeds window.__pydbplayProfiles in the page HTML.

    Asserts that:
    - The profiles bootstrap variable is present.
    - The profile name and active conn_id are embedded.
    - No sensitive fields (password_encrypted / password) leak into the HTML.
    """
    db_path = tmp_path / "target.db"
    _seed_target_db(db_path)
    client = _make_client(tmp_path)

    with client:
        conn_id = _register_connection(client, db_path)
        resp = client.get(f"/c/{conn_id}/browse/things")

    assert resp.status_code == 200
    assert "window.__pydbplayProfiles" in resp.text
    # Profile name must appear in the embedded JSON
    assert '"Test SQLite"' in resp.text
    # Active connection id must be embedded
    assert f"window.__pydbplayActiveConnId = {conn_id}" in resp.text
    # Sensitive fields must NOT appear in the page
    assert "password_encrypted" not in resp.text
    assert "password" not in resp.text
