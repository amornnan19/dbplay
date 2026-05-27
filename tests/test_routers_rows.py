"""Tests for /api/c/{conn_id}/rows/{table} and /c/{conn_id}/browse/{table} endpoints."""

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
    """Create a SQLite DB with a users(id PK, name, age) table and 3 rows."""
    with sqlite3.connect(db_path) as cx:
        cx.execute(
            """
            CREATE TABLE users (
                id   INTEGER PRIMARY KEY,
                name TEXT    NOT NULL,
                age  INTEGER
            )
            """
        )
        cx.execute("INSERT INTO users (id, name, age) VALUES (1, 'Alice', 30)")
        cx.execute("INSERT INTO users (id, name, age) VALUES (2, 'Bob', 25)")
        cx.execute("INSERT INTO users (id, name, age) VALUES (3, 'Carol', 35)")
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
    return repo.list_connections()[-1].id


# ---------------------------------------------------------------------------
# GET /api/c/{conn_id}/rows/users — basic browse
# ---------------------------------------------------------------------------


def test_browse_rows_returns_grid(tmp_path: Path) -> None:
    """GET rows → 200, column headers and data values present."""
    db_path = tmp_path / "target.db"
    _seed_target_db(db_path)
    client = _make_client(tmp_path)

    with client:
        conn_id = _register_connection(client, db_path)
        resp = client.get(f"/api/c/{conn_id}/rows/users")

    assert resp.status_code == 200
    assert "name" in resp.text
    assert "age" in resp.text
    assert "Alice" in resp.text
    assert "Bob" in resp.text
    assert "Carol" in resp.text


def test_browse_rows_pagination_has_next(tmp_path: Path) -> None:
    """GET rows with page_size=2 → has_next, only 2 rows returned."""
    db_path = tmp_path / "target.db"
    _seed_target_db(db_path)
    client = _make_client(tmp_path)

    with client:
        conn_id = _register_connection(client, db_path)
        resp = client.get(f"/api/c/{conn_id}/rows/users?page=1&page_size=2")

    assert resp.status_code == 200
    # has_next → Next button should be present and enabled
    assert "Next" in resp.text
    # Only 2 data rows rendered (Alice, Bob — Carol on page 2)
    assert "Alice" in resp.text
    assert "Bob" in resp.text
    # Carol should NOT appear on page 1
    assert "Carol" not in resp.text


def test_browse_rows_sort_desc(tmp_path: Path) -> None:
    """GET rows with sort=age&dir=DESC → response reflects sort."""
    db_path = tmp_path / "target.db"
    _seed_target_db(db_path)
    client = _make_client(tmp_path)

    with client:
        conn_id = _register_connection(client, db_path)
        resp = client.get(f"/api/c/{conn_id}/rows/users?sort=age&dir=DESC")

    assert resp.status_code == 200
    # Sort arrow indicator should appear for the age column
    assert "▼" in resp.text or "dir=ASC" in resp.text


def test_browse_rows_filter(tmp_path: Path) -> None:
    """GET rows with f_name=Alice → only Alice row returned."""
    db_path = tmp_path / "target.db"
    _seed_target_db(db_path)
    client = _make_client(tmp_path)

    with client:
        conn_id = _register_connection(client, db_path)
        resp = client.get(f"/api/c/{conn_id}/rows/users?f_name=Alice")

    assert resp.status_code == 200
    assert "Alice" in resp.text
    assert "Bob" not in resp.text
    assert "Carol" not in resp.text


# ---------------------------------------------------------------------------
# PATCH /api/c/{conn_id}/rows/users — inline cell update
# ---------------------------------------------------------------------------


def test_patch_row_updates_value(tmp_path: Path) -> None:
    """PATCH with column=name&value=NewName&pk_id=1 → 200, updated value in <tr>."""
    db_path = tmp_path / "target.db"
    _seed_target_db(db_path)
    client = _make_client(tmp_path)

    with client:
        conn_id = _register_connection(client, db_path)
        resp = client.patch(
            f"/api/c/{conn_id}/rows/users",
            data={"column": "name", "value": "AliceUpdated", "pk_id": "1"},
        )

    assert resp.status_code == 200
    assert "AliceUpdated" in resp.text

    # Verify persisted in DB
    with sqlite3.connect(db_path) as cx:
        row = cx.execute("SELECT name FROM users WHERE id = 1").fetchone()
    assert row is not None
    assert row[0] == "AliceUpdated"


def test_patch_unknown_column_returns_400(tmp_path: Path) -> None:
    """PATCH with an unknown column → 400 error partial, not 500."""
    db_path = tmp_path / "target.db"
    _seed_target_db(db_path)
    client = _make_client(tmp_path)

    with client:
        conn_id = _register_connection(client, db_path)
        resp = client.patch(
            f"/api/c/{conn_id}/rows/users",
            data={"column": "nonexistent_col", "value": "x", "pk_id": "1"},
        )

    assert resp.status_code == 400
    assert "error" in resp.text.lower() or "nonexistent_col" in resp.text.lower()


def test_patch_pk_column_returns_400(tmp_path: Path) -> None:
    """PATCH attempting to change the PK column → 400, data unchanged."""
    db_path = tmp_path / "target.db"
    _seed_target_db(db_path)
    client = _make_client(tmp_path)

    with client:
        conn_id = _register_connection(client, db_path)
        resp = client.patch(
            f"/api/c/{conn_id}/rows/users",
            data={"column": "id", "value": "99", "pk_id": "1"},
        )

    assert resp.status_code == 400

    # Data must be unchanged
    with sqlite3.connect(db_path) as cx:
        row = cx.execute("SELECT id FROM users WHERE id = 1").fetchone()
    assert row is not None


# ---------------------------------------------------------------------------
# POST /api/c/{conn_id}/rows/users — insert
# ---------------------------------------------------------------------------


def test_insert_row_adds_new_row(tmp_path: Path) -> None:
    """POST new column values → grid contains the new row."""
    db_path = tmp_path / "target.db"
    _seed_target_db(db_path)
    client = _make_client(tmp_path)

    with client:
        conn_id = _register_connection(client, db_path)
        resp = client.post(
            f"/api/c/{conn_id}/rows/users",
            data={"name": "Dave", "age": "40"},
        )

    assert resp.status_code == 200
    assert "Dave" in resp.text

    # Verify persisted
    with sqlite3.connect(db_path) as cx:
        row = cx.execute("SELECT name, age FROM users WHERE name = 'Dave'").fetchone()
    assert row is not None
    assert row[0] == "Dave"


# ---------------------------------------------------------------------------
# DELETE /api/c/{conn_id}/rows/users — delete
# ---------------------------------------------------------------------------


def test_delete_row_returns_204(tmp_path: Path) -> None:
    """DELETE with pk_id=1 → 204 and row is gone from DB."""
    db_path = tmp_path / "target.db"
    _seed_target_db(db_path)
    client = _make_client(tmp_path)

    with client:
        conn_id = _register_connection(client, db_path)
        resp = client.request(
            "DELETE",
            f"/api/c/{conn_id}/rows/users",
            data={"pk_id": "1"},
        )

    assert resp.status_code == 204

    with sqlite3.connect(db_path) as cx:
        row = cx.execute("SELECT id FROM users WHERE id = 1").fetchone()
    assert row is None


# ---------------------------------------------------------------------------
# Read-only connection — PATCH/POST/DELETE must be rejected
# ---------------------------------------------------------------------------


def test_read_only_patch_rejected(tmp_path: Path) -> None:
    """PATCH on a read-only connection → error status (not 500), data unchanged."""
    db_path = tmp_path / "target.db"
    _seed_target_db(db_path)
    client = _make_client(tmp_path)

    with client:
        conn_id = _register_connection(client, db_path, read_only=True)
        resp = client.patch(
            f"/api/c/{conn_id}/rows/users",
            data={"column": "name", "value": "X", "pk_id": "1"},
        )

    assert resp.status_code in {400, 403}

    with sqlite3.connect(db_path) as cx:
        row = cx.execute("SELECT name FROM users WHERE id = 1").fetchone()
    assert row is not None
    assert row[0] == "Alice"


def test_read_only_insert_rejected(tmp_path: Path) -> None:
    """POST on a read-only connection → error status, row count unchanged."""
    db_path = tmp_path / "target.db"
    _seed_target_db(db_path)
    client = _make_client(tmp_path)

    with client:
        conn_id = _register_connection(client, db_path, read_only=True)
        resp = client.post(
            f"/api/c/{conn_id}/rows/users",
            data={"name": "Eve", "age": "22"},
        )

    assert resp.status_code in {400, 403}

    with sqlite3.connect(db_path) as cx:
        count = cx.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    assert count == 3


def test_read_only_delete_rejected(tmp_path: Path) -> None:
    """DELETE on a read-only connection → error status, row count unchanged."""
    db_path = tmp_path / "target.db"
    _seed_target_db(db_path)
    client = _make_client(tmp_path)

    with client:
        conn_id = _register_connection(client, db_path, read_only=True)
        resp = client.request(
            "DELETE",
            f"/api/c/{conn_id}/rows/users",
            data={"pk_id": "1"},
        )

    assert resp.status_code in {400, 403}

    with sqlite3.connect(db_path) as cx:
        count = cx.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    assert count == 3


# ---------------------------------------------------------------------------
# Injection / escaping
# ---------------------------------------------------------------------------


def test_bad_table_name_returns_400(tmp_path: Path) -> None:
    """GET /rows/<nonexistent-table> → 400, not 500."""
    db_path = tmp_path / "target.db"
    _seed_target_db(db_path)
    client = _make_client(tmp_path)

    with client:
        conn_id = _register_connection(client, db_path)
        resp = client.get(f"/api/c/{conn_id}/rows/nonexistent_table_xyz")

    assert resp.status_code == 400


def test_bad_sort_column_returns_400(tmp_path: Path) -> None:
    """GET /rows/users?sort=<bad_col> → 400, not 500."""
    db_path = tmp_path / "target.db"
    _seed_target_db(db_path)
    client = _make_client(tmp_path)

    with client:
        conn_id = _register_connection(client, db_path)
        resp = client.get(f"/api/c/{conn_id}/rows/users?sort=injected_col")

    assert resp.status_code == 400


def test_xss_cell_value_is_escaped(tmp_path: Path) -> None:
    """Row whose name contains <script>alert(1)</script> renders as HTML-escaped."""
    db_path = tmp_path / "target.db"
    _seed_target_db(db_path)
    xss_name = "<script>alert(1)</script>"

    with sqlite3.connect(db_path) as cx:
        cx.execute("INSERT INTO users (id, name, age) VALUES (99, ?, 0)", (xss_name,))
        cx.commit()

    client = _make_client(tmp_path)

    with client:
        conn_id = _register_connection(client, db_path)
        resp = client.get(f"/api/c/{conn_id}/rows/users")

    assert resp.status_code == 200
    # Escaped form must be present
    assert "&lt;script&gt;" in resp.text, "Escaped form must appear"
    # Raw form must NOT appear
    assert "<script>alert(1)</script>" not in resp.text, "Raw <script> must not appear"


def test_xss_attribute_breakout_in_grid(tmp_path: Path) -> None:
    """Attribute-injection payloads in cell values are escaped in data-* and hx-vals."""
    db_path = tmp_path / "target.db"
    _seed_target_db(db_path)
    # Double-quote breakout: would escape from data-value="..." attribute
    dq_payload = '"><img src=x onerror=alert(1)>'
    # Single-quote breakout: would escape from single-quoted attribute contexts
    sq_payload = "'><img src=x onerror=alert(1)>"

    with sqlite3.connect(db_path) as cx:
        cx.execute("INSERT INTO users (id, name, age) VALUES (100, ?, 0)", (dq_payload,))
        cx.execute("INSERT INTO users (id, name, age) VALUES (101, ?, 0)", (sq_payload,))
        cx.commit()

    client = _make_client(tmp_path)

    with client:
        conn_id = _register_connection(client, db_path)
        resp = client.get(f"/api/c/{conn_id}/rows/users")

    assert resp.status_code == 200
    html = resp.text

    # Raw attribute-breakout sequences must NOT appear
    assert '"><img src=x onerror=alert(1)>' not in html, "Raw double-quote breakout must not appear"
    assert "'><img src=x onerror=alert(1)>" not in html, "Raw single-quote breakout must not appear"

    # The escaped forms (&#34; / &amp;quot; / &lt;) must be present — Jinja2 uses &#34; for "
    # in attribute contexts and &lt; for <
    assert "&#34;" in html or "&quot;" in html, "Escaped double-quote must appear"
    assert "&lt;img" in html, "Escaped < before img must appear"


def test_xss_attribute_breakout_in_patch_response(tmp_path: Path) -> None:
    """PATCH response (row.html) escapes attribute-breakout payloads in data-* and hx-vals."""
    db_path = tmp_path / "target.db"
    _seed_target_db(db_path)
    dq_payload = '"><img src=x onerror=alert(1)>'

    client = _make_client(tmp_path)

    with client:
        conn_id = _register_connection(client, db_path)
        # Update Alice's name to the XSS payload — PATCH returns row.html partial
        resp = client.patch(
            f"/api/c/{conn_id}/rows/users",
            data={"column": "name", "value": dq_payload, "pk_id": "1"},
        )

    assert resp.status_code == 200
    html = resp.text

    # Raw breakout must not appear in the returned <tr>
    assert '"><img src=x onerror=alert(1)>' not in html, (
        "Raw double-quote breakout must not appear in PATCH response"
    )
    assert "&lt;img" in html, "Escaped < before img must appear in PATCH response"


# ---------------------------------------------------------------------------
# GET /c/{conn_id}/browse/{table} — browse page
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# PATCH with non-existent PK → 404
# ---------------------------------------------------------------------------


def test_patch_nonexistent_pk_returns_404(tmp_path: Path) -> None:
    """PATCH with a PK that matches no row → 404 error partial, not a corrupt 200."""
    db_path = tmp_path / "target.db"
    _seed_target_db(db_path)
    client = _make_client(tmp_path)

    with client:
        conn_id = _register_connection(client, db_path)
        resp = client.patch(
            f"/api/c/{conn_id}/rows/users",
            data={"column": "name", "value": "Ghost", "pk_id": "9999"},
        )

    assert resp.status_code == 404
    assert "not found" in resp.text.lower() or "deleted" in resp.text.lower()


# ---------------------------------------------------------------------------
# Composite PK — PATCH and DELETE target only the intended row
# ---------------------------------------------------------------------------


def _seed_composite_pk_db(db_path: Path) -> None:
    """Create a SQLite DB with an order_items(order_id, product_id, qty) composite-PK table."""
    with sqlite3.connect(db_path) as cx:
        cx.execute(
            """
            CREATE TABLE order_items (
                order_id   INTEGER NOT NULL,
                product_id INTEGER NOT NULL,
                qty        INTEGER NOT NULL,
                PRIMARY KEY (order_id, product_id)
            )
            """
        )
        cx.execute("INSERT INTO order_items VALUES (1, 10, 5)")
        cx.execute("INSERT INTO order_items VALUES (1, 20, 3)")
        cx.execute("INSERT INTO order_items VALUES (2, 10, 1)")
        cx.commit()


def test_composite_pk_patch_targets_correct_row(tmp_path: Path) -> None:
    """PATCH on a composite-PK table updates only the targeted row; others unchanged."""
    db_path = tmp_path / "target.db"
    _seed_composite_pk_db(db_path)
    client = _make_client(tmp_path)

    with client:
        conn_id = _register_connection(client, db_path)
        resp = client.patch(
            f"/api/c/{conn_id}/rows/order_items",
            data={
                "column": "qty",
                "value": "99",
                "pk_order_id": "1",
                "pk_product_id": "10",
            },
        )

    assert resp.status_code == 200
    assert "99" in resp.text

    # Verify only the targeted row changed
    with sqlite3.connect(db_path) as cx:
        row = cx.execute(
            "SELECT qty FROM order_items WHERE order_id=1 AND product_id=10"
        ).fetchone()
        assert row is not None and row[0] == 99

        # Other rows must be untouched
        row2 = cx.execute(
            "SELECT qty FROM order_items WHERE order_id=1 AND product_id=20"
        ).fetchone()
        assert row2 is not None and row2[0] == 3

        row3 = cx.execute(
            "SELECT qty FROM order_items WHERE order_id=2 AND product_id=10"
        ).fetchone()
        assert row3 is not None and row3[0] == 1


def test_composite_pk_delete_targets_correct_row(tmp_path: Path) -> None:
    """DELETE on a composite-PK table removes only the targeted row; others unchanged."""
    db_path = tmp_path / "target.db"
    _seed_composite_pk_db(db_path)
    client = _make_client(tmp_path)

    with client:
        conn_id = _register_connection(client, db_path)
        resp = client.request(
            "DELETE",
            f"/api/c/{conn_id}/rows/order_items",
            data={"pk_order_id": "1", "pk_product_id": "10"},
        )

    assert resp.status_code == 204

    with sqlite3.connect(db_path) as cx:
        # Targeted row must be gone
        gone = cx.execute(
            "SELECT qty FROM order_items WHERE order_id=1 AND product_id=10"
        ).fetchone()
        assert gone is None

        # Other rows must still exist
        row2 = cx.execute(
            "SELECT qty FROM order_items WHERE order_id=1 AND product_id=20"
        ).fetchone()
        assert row2 is not None and row2[0] == 3

        row3 = cx.execute(
            "SELECT qty FROM order_items WHERE order_id=2 AND product_id=10"
        ).fetchone()
        assert row3 is not None and row3[0] == 1


def test_browse_page_renders(tmp_path: Path) -> None:
    """GET /c/{conn_id}/browse/users → 200, grid container + table name present."""
    db_path = tmp_path / "target.db"
    _seed_target_db(db_path)
    client = _make_client(tmp_path)

    with client:
        conn_id = _register_connection(client, db_path)
        resp = client.get(f"/c/{conn_id}/browse/users")

    assert resp.status_code == 200
    assert "users" in resp.text
    assert "row-grid" in resp.text


def test_browse_page_missing_conn_returns_404(tmp_path: Path) -> None:
    """GET /c/9999/browse/users → 404 when connection doesn't exist."""
    client = _make_client(tmp_path)

    with client:
        resp = client.get("/c/9999/browse/users")

    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Missing connection → 404 on API endpoints
# ---------------------------------------------------------------------------


def test_browse_api_missing_conn_returns_404(tmp_path: Path) -> None:
    """GET /api/c/9999/rows/users → 404."""
    client = _make_client(tmp_path)

    with client:
        resp = client.get("/api/c/9999/rows/users")

    assert resp.status_code == 404


def test_patch_api_missing_conn_returns_404(tmp_path: Path) -> None:
    """PATCH /api/c/9999/rows/users → 404."""
    client = _make_client(tmp_path)

    with client:
        resp = client.patch(
            "/api/c/9999/rows/users",
            data={"column": "name", "value": "x", "pk_id": "1"},
        )

    assert resp.status_code == 404


def test_delete_api_missing_conn_returns_404(tmp_path: Path) -> None:
    """DELETE /api/c/9999/rows/users → 404."""
    client = _make_client(tmp_path)

    with client:
        resp = client.request("DELETE", "/api/c/9999/rows/users", data={"pk_id": "1"})

    assert resp.status_code == 404
