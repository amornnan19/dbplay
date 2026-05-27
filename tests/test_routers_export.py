"""Tests for POST /api/c/{conn_id}/export endpoint (Phase 3).

All tests use an in-process SQLite target database so no Docker is required
(no @pytest.mark.integration needed here).
"""

import csv
import io
import json
import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

from pydbplay.app.main import create_app

# ---------------------------------------------------------------------------
# Helpers — mirrors pattern from test_routers_query.py
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
    """Create a target SQLite DB with a 'items' table including tricky values."""
    with sqlite3.connect(db_path) as cx:
        cx.execute(
            """
            CREATE TABLE items (
                id    INTEGER PRIMARY KEY,
                name  TEXT NOT NULL,
                price REAL
            )
            """
        )
        # Include a value with a comma and a value with a quote to stress CSV/SQL quoting
        cx.execute("INSERT INTO items (id, name, price) VALUES (1, 'Widget, Deluxe', 9.99)")
        cx.execute('INSERT INTO items (id, name, price) VALUES (2, "Bob\'s Gadget", 14.50)')
        cx.execute("INSERT INTO items (id, name, price) VALUES (3, 'Plain Item', 1.00)")
        cx.commit()


def _register_connection(client: TestClient, db_path: Path, *, read_only: bool = False) -> int:
    """Create a connection profile via POST and return its id."""
    data: dict[str, str] = {
        "name": "Export Test SQLite",
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
# CSV export
# ---------------------------------------------------------------------------


def test_export_query_csv(tmp_path: Path) -> None:
    """POST export?format=csv with sql= returns 200, text/csv, attachment, parseable rows."""
    db_path = tmp_path / "target.db"
    _seed_target_db(db_path)
    client = _make_client(tmp_path)

    with client:
        conn_id = _register_connection(client, db_path)
        resp = client.post(
            f"/api/c/{conn_id}/export?format=csv",
            data={"sql": "SELECT * FROM items"},
        )

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
    assert "attachment" in resp.headers["content-disposition"]
    assert ".csv" in resp.headers["content-disposition"]

    # Body must parse as valid CSV with the expected rows
    reader = csv.DictReader(io.StringIO(resp.text))
    rows = list(reader)
    assert len(rows) == 3
    names = [r["name"] for r in rows]
    assert "Widget, Deluxe" in names  # comma inside value — must be quoted
    assert "Bob's Gadget" in names  # single quote inside value


# ---------------------------------------------------------------------------
# JSON export
# ---------------------------------------------------------------------------


def test_export_query_json(tmp_path: Path) -> None:
    """POST export?format=json returns application/json and a valid JSON array."""
    db_path = tmp_path / "target.db"
    _seed_target_db(db_path)
    client = _make_client(tmp_path)

    with client:
        conn_id = _register_connection(client, db_path)
        resp = client.post(
            f"/api/c/{conn_id}/export?format=json",
            data={"sql": "SELECT * FROM items"},
        )

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/json")
    assert "attachment" in resp.headers["content-disposition"]
    assert ".json" in resp.headers["content-disposition"]

    data = json.loads(resp.text)
    assert isinstance(data, list)
    assert len(data) == 3
    assert any(row["name"] == "Widget, Deluxe" for row in data)


# ---------------------------------------------------------------------------
# SQL export
# ---------------------------------------------------------------------------


def test_export_query_sql(tmp_path: Path) -> None:
    """POST export?format=sql returns INSERT statements for each row."""
    db_path = tmp_path / "target.db"
    _seed_target_db(db_path)
    client = _make_client(tmp_path)

    with client:
        conn_id = _register_connection(client, db_path)
        resp = client.post(
            f"/api/c/{conn_id}/export?format=sql",
            data={"sql": "SELECT * FROM items"},
        )

    assert resp.status_code == 200
    assert "attachment" in resp.headers["content-disposition"]
    assert ".sql" in resp.headers["content-disposition"]

    body = resp.text
    # Three INSERT statements
    assert body.count("INSERT INTO") == 3
    # Single-quote-escaped apostrophe in Bob's Gadget
    assert "Bob''s Gadget" in body


# ---------------------------------------------------------------------------
# Table export (export_table)
# ---------------------------------------------------------------------------


def test_export_table_csv(tmp_path: Path) -> None:
    """POST export?format=csv with table= exports all rows from the table."""
    db_path = tmp_path / "target.db"
    _seed_target_db(db_path)
    client = _make_client(tmp_path)

    with client:
        conn_id = _register_connection(client, db_path)
        resp = client.post(
            f"/api/c/{conn_id}/export?format=csv",
            data={"table": "items"},
        )

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
    # Filename should use the table name
    assert "items" in resp.headers["content-disposition"]

    reader = csv.DictReader(io.StringIO(resp.text))
    rows = list(reader)
    assert len(rows) == 3


def test_export_table_json(tmp_path: Path) -> None:
    """POST export?format=json with table= exports all rows as JSON."""
    db_path = tmp_path / "target.db"
    _seed_target_db(db_path)
    client = _make_client(tmp_path)

    with client:
        conn_id = _register_connection(client, db_path)
        resp = client.post(
            f"/api/c/{conn_id}/export?format=json",
            data={"table": "items"},
        )

    assert resp.status_code == 200
    data = json.loads(resp.text)
    assert len(data) == 3


def test_export_table_sql(tmp_path: Path) -> None:
    """POST export?format=sql with table= produces INSERT statements."""
    db_path = tmp_path / "target.db"
    _seed_target_db(db_path)
    client = _make_client(tmp_path)

    with client:
        conn_id = _register_connection(client, db_path)
        resp = client.post(
            f"/api/c/{conn_id}/export?format=sql",
            data={"table": "items"},
        )

    assert resp.status_code == 200
    body = resp.text
    assert body.count("INSERT INTO") == 3
    # Table name should appear in INSERT
    assert "items" in body


# ---------------------------------------------------------------------------
# Error cases
# ---------------------------------------------------------------------------


def test_export_bad_format_returns_400(tmp_path: Path) -> None:
    """POST export?format=xml returns 400 or 422, not 500."""
    db_path = tmp_path / "target.db"
    _seed_target_db(db_path)
    client = _make_client(tmp_path)

    with client:
        conn_id = _register_connection(client, db_path)
        resp = client.post(
            f"/api/c/{conn_id}/export?format=xml",
            data={"sql": "SELECT * FROM items"},
        )

    assert resp.status_code in {400, 422}


def test_export_missing_connection_returns_404(tmp_path: Path) -> None:
    """POST export for a non-existent connection returns 404."""
    client = _make_client(tmp_path)

    with client:
        resp = client.post(
            "/api/c/9999/export?format=csv",
            data={"sql": "SELECT 1"},
        )

    assert resp.status_code == 404


def test_export_read_only_connection_can_export(tmp_path: Path) -> None:
    """A read-only connection can still export (SELECT is allowed)."""
    db_path = tmp_path / "target.db"
    _seed_target_db(db_path)
    client = _make_client(tmp_path)

    with client:
        conn_id = _register_connection(client, db_path, read_only=True)
        resp = client.post(
            f"/api/c/{conn_id}/export?format=csv",
            data={"sql": "SELECT * FROM items"},
        )

    assert resp.status_code == 200
    reader = csv.DictReader(io.StringIO(resp.text))
    assert len(list(reader)) == 3


def test_export_unknown_table_returns_400(tmp_path: Path) -> None:
    """POST export with a non-existent table= returns 400 (UnknownIdentifierError)."""
    db_path = tmp_path / "target.db"
    _seed_target_db(db_path)
    client = _make_client(tmp_path)

    with client:
        conn_id = _register_connection(client, db_path)
        resp = client.post(
            f"/api/c/{conn_id}/export?format=csv",
            data={"table": "nonexistent_table_xyz"},
        )

    assert resp.status_code == 400


def test_export_no_data_field_returns_400(tmp_path: Path) -> None:
    """POST export with neither sql nor table returns 400."""
    db_path = tmp_path / "target.db"
    _seed_target_db(db_path)
    client = _make_client(tmp_path)

    with client:
        conn_id = _register_connection(client, db_path)
        resp = client.post(
            f"/api/c/{conn_id}/export?format=csv",
            data={},
        )

    assert resp.status_code == 400


def test_export_both_sql_and_table_returns_400(tmp_path: Path) -> None:
    """POST export with both sql and table fields returns 400."""
    db_path = tmp_path / "target.db"
    _seed_target_db(db_path)
    client = _make_client(tmp_path)

    with client:
        conn_id = _register_connection(client, db_path)
        resp = client.post(
            f"/api/c/{conn_id}/export?format=csv",
            data={"sql": "SELECT 1", "table": "items"},
        )

    assert resp.status_code == 400


def test_export_bad_sql_returns_400(tmp_path: Path) -> None:
    """POST export with sql= referencing a non-existent table returns 400, not 200.

    Before the generator-priming fix this would return 200 with an empty/corrupt
    body because StreamingResponse commits headers before the generator runs.
    """
    db_path = tmp_path / "target.db"
    _seed_target_db(db_path)
    client = _make_client(tmp_path)

    with client:
        conn_id = _register_connection(client, db_path)
        resp = client.post(
            f"/api/c/{conn_id}/export?format=csv",
            data={"sql": "SELECT * FROM no_such_table_xyz"},
        )

    assert resp.status_code == 400
    # Body must not be an empty CSV — it should be a JSON error detail
    assert resp.text.strip() != ""


def test_export_table_special_chars_in_name(tmp_path: Path) -> None:
    """Export a table whose name contains a double-quote character.

    The Content-Disposition header must be well-formed: no bare ``"`` breaking
    the ``filename="..."`` token, and an RFC 5987 ``filename*`` field present.
    """
    db_path = tmp_path / "target.db"
    # Create a table with a double-quote in its name via raw sqlite3
    with sqlite3.connect(db_path) as cx:
        cx.execute('CREATE TABLE "weird""name" (id INTEGER PRIMARY KEY, val TEXT)')
        cx.execute('INSERT INTO "weird""name" VALUES (1, \'hello\')')
        cx.commit()

    client = _make_client(tmp_path)

    with client:
        conn_id = _register_connection(client, db_path)
        resp = client.post(
            f"/api/c/{conn_id}/export?format=csv",
            data={"table": 'weird"name'},
        )

    assert resp.status_code == 200
    disposition = resp.headers["content-disposition"]
    # The ascii fallback filename must not contain a bare double-quote
    # (which would break the quoted-string syntax)
    import re as _re

    # Extract the filename="..." token and verify no unescaped " inside
    m = _re.search(r'filename="([^"]*)"', disposition)
    assert m is not None, f"No valid filename= token in: {disposition!r}"
    # RFC 5987 filename* must be present
    assert "filename*=" in disposition
