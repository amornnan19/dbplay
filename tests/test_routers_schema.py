"""Tests for /api/c/{conn_id}/schemas, /tables, /tables/{table} endpoints."""

import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

from pydbplay.app.main import create_app

# ---------------------------------------------------------------------------
# Helpers — mirror patterns from test_routers_query.py
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
    """Create a SQLite DB with two tables.

    - ``departments`` has a PK, and gets a unique index on ``name``.
    - ``employees`` has a PK and a FK to ``departments``.
    """
    with sqlite3.connect(db_path) as cx:
        cx.executescript(
            """
            PRAGMA foreign_keys = ON;

            CREATE TABLE departments (
                id   INTEGER PRIMARY KEY,
                name TEXT    NOT NULL
            );

            CREATE UNIQUE INDEX idx_dept_name ON departments(name);

            CREATE TABLE employees (
                id         INTEGER PRIMARY KEY,
                name       TEXT    NOT NULL,
                dept_id    INTEGER,
                FOREIGN KEY (dept_id) REFERENCES departments(id) ON DELETE SET NULL
            );

            INSERT INTO departments(id, name) VALUES (1, 'Engineering'), (2, 'Sales');
            INSERT INTO employees(id, name, dept_id) VALUES (1, 'Alice', 1), (2, 'Bob', 2);
            """
        )


def _register_sqlite_connection(client: TestClient, db_path: Path) -> int:
    """POST a sqlite connection and return its id."""
    client.post(
        "/api/connections",
        data={
            "name": "Schema Test DB",
            "engine": "sqlite",
            "database": str(db_path),
        },
    )
    from pydbplay.db.repository import Repository

    repo: Repository = client.app.state.repository  # type: ignore[attr-defined]
    return repo.list_connections()[0].id


def _register_mysql_connection(client: TestClient) -> int:
    """POST a mysql connection (engine unsupported) and return its id."""
    client.post(
        "/api/connections",
        data={
            "name": "MySQL Connection",
            "engine": "mysql",
            "database": "mydb",
            "host": "localhost",
            "port": "3306",
            "username": "mysqluser",
        },
    )
    from pydbplay.db.repository import Repository

    repo: Repository = client.app.state.repository  # type: ignore[attr-defined]
    conns = repo.list_connections()
    # Return the mysql one (last created)
    return conns[-1].id


# ---------------------------------------------------------------------------
# GET /api/c/{conn_id}/schemas
# ---------------------------------------------------------------------------


def test_list_schemas_returns_200_with_main(tmp_path: Path) -> None:
    """GET /schemas returns 200 and contains 'main' for a SQLite connection."""
    db_path = tmp_path / "target.db"
    _seed_target_db(db_path)
    client = _make_client(tmp_path)

    with client:
        conn_id = _register_sqlite_connection(client, db_path)
        resp = client.get(f"/api/c/{conn_id}/schemas")

    assert resp.status_code == 200
    assert "main" in resp.text


def test_list_schemas_missing_conn_returns_404(tmp_path: Path) -> None:
    """GET /schemas for conn_id=9999 returns 404."""
    client = _make_client(tmp_path)

    with client:
        resp = client.get("/api/c/9999/schemas")

    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET /api/c/{conn_id}/tables
# ---------------------------------------------------------------------------


def test_list_tables_returns_both_table_names(tmp_path: Path) -> None:
    """GET /tables lists both seeded table names in the HTML."""
    db_path = tmp_path / "target.db"
    _seed_target_db(db_path)
    client = _make_client(tmp_path)

    with client:
        conn_id = _register_sqlite_connection(client, db_path)
        resp = client.get(f"/api/c/{conn_id}/tables")

    assert resp.status_code == 200
    assert "departments" in resp.text
    assert "employees" in resp.text


def test_list_tables_data_select_contains_quoted_sql(tmp_path: Path) -> None:
    """GET /tables — each row's data-select has a properly quoted SELECT snippet.

    Jinja2 autoescape encodes double-quotes in attribute values as &#34;, so
    we check for the escaped form as it appears in the raw HTML.
    """
    db_path = tmp_path / "target.db"
    _seed_target_db(db_path)
    client = _make_client(tmp_path)

    with client:
        conn_id = _register_sqlite_connection(client, db_path)
        resp = client.get(f"/api/c/{conn_id}/tables")

    assert resp.status_code == 200
    # Jinja2 autoescape renders " as &#34; inside HTML attributes.
    # The data-select attribute value contains the server-quoted SQL.
    assert "SELECT * FROM &#34;departments&#34; LIMIT 100" in resp.text
    assert "SELECT * FROM &#34;employees&#34; LIMIT 100" in resp.text


def test_list_tables_missing_conn_returns_404(tmp_path: Path) -> None:
    """GET /tables for conn_id=9999 returns 404."""
    client = _make_client(tmp_path)

    with client:
        resp = client.get("/api/c/9999/tables")

    assert resp.status_code == 404


def test_list_tables_unsupported_engine_returns_422(tmp_path: Path) -> None:
    """GET /tables for a mysql connection returns 422 (UnsupportedEngineError)."""
    client = _make_client(tmp_path)

    with client:
        conn_id = _register_mysql_connection(client)
        resp = client.get(f"/api/c/{conn_id}/tables")

    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# GET /api/c/{conn_id}/tables/{table}
# ---------------------------------------------------------------------------


def test_describe_table_shows_columns(tmp_path: Path) -> None:
    """GET /tables/employees shows all column names."""
    db_path = tmp_path / "target.db"
    _seed_target_db(db_path)
    client = _make_client(tmp_path)

    with client:
        conn_id = _register_sqlite_connection(client, db_path)
        resp = client.get(f"/api/c/{conn_id}/tables/employees")

    assert resp.status_code == 200
    assert "id" in resp.text
    assert "name" in resp.text
    assert "dept_id" in resp.text


def test_describe_table_shows_pk_indicator(tmp_path: Path) -> None:
    """GET /tables/departments shows PK badge for the id column."""
    db_path = tmp_path / "target.db"
    _seed_target_db(db_path)
    client = _make_client(tmp_path)

    with client:
        conn_id = _register_sqlite_connection(client, db_path)
        resp = client.get(f"/api/c/{conn_id}/tables/departments")

    assert resp.status_code == 200
    assert "PK" in resp.text


def test_describe_table_shows_index(tmp_path: Path) -> None:
    """GET /tables/departments shows the unique index idx_dept_name."""
    db_path = tmp_path / "target.db"
    _seed_target_db(db_path)
    client = _make_client(tmp_path)

    with client:
        conn_id = _register_sqlite_connection(client, db_path)
        resp = client.get(f"/api/c/{conn_id}/tables/departments")

    assert resp.status_code == 200
    assert "idx_dept_name" in resp.text


def test_describe_table_shows_foreign_key(tmp_path: Path) -> None:
    """GET /tables/employees shows the FK referencing departments."""
    db_path = tmp_path / "target.db"
    _seed_target_db(db_path)
    client = _make_client(tmp_path)

    with client:
        conn_id = _register_sqlite_connection(client, db_path)
        resp = client.get(f"/api/c/{conn_id}/tables/employees")

    assert resp.status_code == 200
    assert "departments" in resp.text


def test_describe_table_missing_conn_returns_404(tmp_path: Path) -> None:
    """GET /tables/employees for conn_id=9999 returns 404."""
    client = _make_client(tmp_path)

    with client:
        resp = client.get("/api/c/9999/tables/employees")

    assert resp.status_code == 404


def test_describe_table_unsupported_engine_returns_422(tmp_path: Path) -> None:
    """GET /tables/sometable for a mysql connection returns 422."""
    client = _make_client(tmp_path)

    with client:
        conn_id = _register_mysql_connection(client)
        resp = client.get(f"/api/c/{conn_id}/tables/sometable")

    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# GET /api/c/{conn_id}/schemas — unsupported engine returns 422
# ---------------------------------------------------------------------------


def test_list_schemas_unsupported_engine_returns_422(tmp_path: Path) -> None:
    """GET /schemas for a mysql connection returns 422."""
    client = _make_client(tmp_path)

    with client:
        conn_id = _register_mysql_connection(client)
        resp = client.get(f"/api/c/{conn_id}/schemas")

    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Adversarial table name — HTML escaping (optional per spec)
# ---------------------------------------------------------------------------


def test_table_list_escapes_special_characters(tmp_path: Path) -> None:
    """Table names with HTML-executable payloads are autoescaped in the HTML response."""
    db_path = tmp_path / "target.db"
    # Create a table whose name contains an executable XSS payload.
    # SQLite allows arbitrary names when double-quoted.
    with sqlite3.connect(db_path) as cx:
        cx.execute('CREATE TABLE "<img src=x onerror=alert(1)>" (id INTEGER PRIMARY KEY)')

    client = _make_client(tmp_path)

    with client:
        conn_id = _register_sqlite_connection(client, db_path)
        resp = client.get(f"/api/c/{conn_id}/tables")

    assert resp.status_code == 200
    # The raw executable form must NOT appear anywhere in the response body.
    assert "<img src=x onerror=alert(1)>" not in resp.text
    # Jinja2 autoescape must render the opening tag as &lt;img (inert text).
    assert "&lt;img" in resp.text


def test_table_list_escapes_quote_identifier_doubling(tmp_path: Path) -> None:
    """Table name containing a double-quote is properly HTML-escaped in data-select."""
    db_path = tmp_path / "target.db"
    # SQLite allows a double-quote in a table name when the name itself is
    # quoted — the literal name stored is: a"b
    with sqlite3.connect(db_path) as cx:
        cx.execute('CREATE TABLE "a""b" (id INTEGER PRIMARY KEY)')

    client = _make_client(tmp_path)

    with client:
        conn_id = _register_sqlite_connection(client, db_path)
        resp = client.get(f"/api/c/{conn_id}/tables")

    assert resp.status_code == 200
    # quote_identifier doubles the internal quote → "a""b"
    # Jinja2 autoescape then encodes each " as &#34; inside the HTML attribute:
    # data-select="SELECT * FROM &#34;a&#34;&#34;b&#34; LIMIT 100"
    assert "&#34;a&#34;&#34;b&#34;" in resp.text
