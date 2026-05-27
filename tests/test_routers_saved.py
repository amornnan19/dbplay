"""Tests for /api/c/{conn_id}/saved-queries endpoints (SPEC §8 Phase 5)."""

import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

from pydbplay.app.main import create_app

# ---------------------------------------------------------------------------
# Helpers (mirrors test_routers_query.py conventions)
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


def _seed_target_db(db_path: Path) -> None:
    """Create a minimal target SQLite DB."""
    with sqlite3.connect(db_path) as cx:
        cx.execute("CREATE TABLE IF NOT EXISTS t (id INTEGER PRIMARY KEY)")
        cx.commit()


def _register_connection(client: TestClient, db_path: Path) -> int:
    """Create a sqlite connection profile via POST and return its id."""
    client.post(
        "/api/connections",
        data={
            "name": "Test SQLite",
            "engine": "sqlite",
            "database": str(db_path),
        },
    )
    from pydbplay.db.repository import Repository

    repo: Repository = client.app.state.repository  # type: ignore[attr-defined]
    return repo.list_connections()[0].id


# ---------------------------------------------------------------------------
# GET /api/c/{conn_id}/saved-queries — empty state
# ---------------------------------------------------------------------------


def test_list_saved_queries_empty(tmp_path: Path) -> None:
    """GET saved-queries with no saved queries returns 200 and empty-state message."""
    db_path = tmp_path / "target.db"
    _seed_target_db(db_path)
    client = _make_client(tmp_path)

    with client:
        conn_id = _register_connection(client, db_path)
        resp = client.get(f"/api/c/{conn_id}/saved-queries")

    assert resp.status_code == 200
    assert "no saved queries" in resp.text.lower()


# ---------------------------------------------------------------------------
# POST /api/c/{conn_id}/saved-queries — create
# ---------------------------------------------------------------------------


def test_create_saved_query_returns_list_with_name(tmp_path: Path) -> None:
    """POST saved-queries creates the query and returns a list containing the name."""
    db_path = tmp_path / "target.db"
    _seed_target_db(db_path)
    client = _make_client(tmp_path)

    with client:
        conn_id = _register_connection(client, db_path)
        resp = client.post(
            f"/api/c/{conn_id}/saved-queries",
            data={"name": "My Query", "sql": "SELECT 1"},
        )

    assert resp.status_code == 200
    assert "My Query" in resp.text


def test_create_saved_query_persists_in_repository(tmp_path: Path) -> None:
    """After POST, repository.list_saved_queries shows the new entry."""
    db_path = tmp_path / "target.db"
    _seed_target_db(db_path)
    client = _make_client(tmp_path)

    with client:
        conn_id = _register_connection(client, db_path)
        client.post(
            f"/api/c/{conn_id}/saved-queries",
            data={"name": "Persisted", "sql": "SELECT 42"},
        )
        from pydbplay.db.repository import Repository

        repo: Repository = client.app.state.repository  # type: ignore[attr-defined]
        saved = repo.list_saved_queries(conn_id)

    assert len(saved) == 1
    assert saved[0].name == "Persisted"
    assert saved[0].sql == "SELECT 42"


def test_create_saved_query_sets_hx_trigger(tmp_path: Path) -> None:
    """POST saved-queries response carries HX-Trigger: saved-changed."""
    db_path = tmp_path / "target.db"
    _seed_target_db(db_path)
    client = _make_client(tmp_path)

    with client:
        conn_id = _register_connection(client, db_path)
        resp = client.post(
            f"/api/c/{conn_id}/saved-queries",
            data={"name": "Trigger Test", "sql": "SELECT 1"},
        )

    assert resp.headers.get("HX-Trigger") == "saved-changed"


# ---------------------------------------------------------------------------
# XSS — saved query name and SQL are HTML-escaped
# ---------------------------------------------------------------------------


def test_saved_query_xss_escaping(tmp_path: Path) -> None:
    """Name and SQL containing <script> are HTML-escaped; raw tag must not appear."""
    db_path = tmp_path / "target.db"
    _seed_target_db(db_path)
    client = _make_client(tmp_path)
    xss_name = "<script>alert(1)</script>"
    xss_sql = "SELECT '<script>xss</script>'"

    with client:
        conn_id = _register_connection(client, db_path)
        resp = client.post(
            f"/api/c/{conn_id}/saved-queries",
            data={"name": xss_name, "sql": xss_sql},
        )

    assert resp.status_code == 200
    assert "&lt;script&gt;" in resp.text, "Escaped form must be present"
    assert "<script>alert(1)</script>" not in resp.text, "Raw <script> name must NOT appear"
    assert "<script>xss</script>" not in resp.text, "Raw <script> sql must NOT appear"


def test_saved_query_sql_in_data_attribute_escaped(tmp_path: Path) -> None:
    """SQL is carried in data-sql attribute and is HTML-escaped there too."""
    db_path = tmp_path / "target.db"
    _seed_target_db(db_path)
    client = _make_client(tmp_path)
    xss_sql = "SELECT '<script>bad</script>'"

    with client:
        conn_id = _register_connection(client, db_path)
        resp = client.post(
            f"/api/c/{conn_id}/saved-queries",
            data={"name": "XSS SQL", "sql": xss_sql},
        )

    assert resp.status_code == 200
    # The data-sql attribute must contain the escaped version
    assert "data-sql=" in resp.text
    assert "<script>bad</script>" not in resp.text


# ---------------------------------------------------------------------------
# DELETE /api/c/{conn_id}/saved-queries/{saved_id}
# ---------------------------------------------------------------------------


def test_delete_saved_query_returns_204(tmp_path: Path) -> None:
    """DELETE saved-queries/{id} returns 204."""
    db_path = tmp_path / "target.db"
    _seed_target_db(db_path)
    client = _make_client(tmp_path)

    with client:
        conn_id = _register_connection(client, db_path)
        client.post(
            f"/api/c/{conn_id}/saved-queries",
            data={"name": "To Delete", "sql": "SELECT 99"},
        )
        from pydbplay.db.repository import Repository

        repo: Repository = client.app.state.repository  # type: ignore[attr-defined]
        saved_id = repo.list_saved_queries(conn_id)[0].id

        resp = client.delete(f"/api/c/{conn_id}/saved-queries/{saved_id}")

    assert resp.status_code == 204


def test_delete_saved_query_gone_from_list(tmp_path: Path) -> None:
    """After DELETE, list_saved_queries returns empty."""
    db_path = tmp_path / "target.db"
    _seed_target_db(db_path)
    client = _make_client(tmp_path)

    with client:
        conn_id = _register_connection(client, db_path)
        client.post(
            f"/api/c/{conn_id}/saved-queries",
            data={"name": "Gone", "sql": "SELECT 0"},
        )
        from pydbplay.db.repository import Repository

        repo: Repository = client.app.state.repository  # type: ignore[attr-defined]
        saved_id = repo.list_saved_queries(conn_id)[0].id
        client.delete(f"/api/c/{conn_id}/saved-queries/{saved_id}")
        remaining = repo.list_saved_queries(conn_id)

    assert remaining == []


def test_delete_sets_hx_trigger(tmp_path: Path) -> None:
    """DELETE saved-queries/{id} response carries HX-Trigger: saved-changed."""
    db_path = tmp_path / "target.db"
    _seed_target_db(db_path)
    client = _make_client(tmp_path)

    with client:
        conn_id = _register_connection(client, db_path)
        client.post(
            f"/api/c/{conn_id}/saved-queries",
            data={"name": "Trigger Del", "sql": "SELECT 1"},
        )
        from pydbplay.db.repository import Repository

        repo: Repository = client.app.state.repository  # type: ignore[attr-defined]
        saved_id = repo.list_saved_queries(conn_id)[0].id
        resp = client.delete(f"/api/c/{conn_id}/saved-queries/{saved_id}")

    assert resp.headers.get("HX-Trigger") == "saved-changed"


# ---------------------------------------------------------------------------
# Missing connection → 404
# ---------------------------------------------------------------------------


def test_list_saved_queries_missing_conn_returns_404(tmp_path: Path) -> None:
    """GET /api/c/9999/saved-queries returns 404 when connection doesn't exist."""
    client = _make_client(tmp_path)

    with client:
        resp = client.get("/api/c/9999/saved-queries")

    assert resp.status_code == 404


def test_create_saved_query_missing_conn_returns_404(tmp_path: Path) -> None:
    """POST /api/c/9999/saved-queries returns 404 when connection doesn't exist."""
    client = _make_client(tmp_path)

    with client:
        resp = client.post(
            "/api/c/9999/saved-queries",
            data={"name": "x", "sql": "SELECT 1"},
        )

    assert resp.status_code == 404


def test_delete_saved_query_missing_conn_returns_404(tmp_path: Path) -> None:
    """DELETE /api/c/9999/saved-queries/1 returns 404 when connection doesn't exist."""
    client = _make_client(tmp_path)

    with client:
        resp = client.delete("/api/c/9999/saved-queries/1")

    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Malformed save — missing required fields → 422
# ---------------------------------------------------------------------------


def test_create_saved_query_missing_name_returns_422(tmp_path: Path) -> None:
    """POST without name returns 422 (validation error)."""
    db_path = tmp_path / "target.db"
    _seed_target_db(db_path)
    client = _make_client(tmp_path)

    with client:
        conn_id = _register_connection(client, db_path)
        resp = client.post(
            f"/api/c/{conn_id}/saved-queries",
            data={"sql": "SELECT 1"},
        )

    assert resp.status_code == 422


def test_create_saved_query_missing_sql_returns_422(tmp_path: Path) -> None:
    """POST without sql returns 422 (validation error)."""
    db_path = tmp_path / "target.db"
    _seed_target_db(db_path)
    client = _make_client(tmp_path)

    with client:
        conn_id = _register_connection(client, db_path)
        resp = client.post(
            f"/api/c/{conn_id}/saved-queries",
            data={"name": "No SQL"},
        )

    assert resp.status_code == 422
