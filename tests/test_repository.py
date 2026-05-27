"""Regression tests for run_migrations()."""

import sqlite3
from pathlib import Path

from pydbplay.db.repository import make_engine, run_migrations


def _table_names(db_path: Path) -> set[str]:
    """Return all user-created table names in the SQLite file (excludes sqlite_* internals)."""
    with sqlite3.connect(db_path) as cx:
        rows = cx.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
    return {row[0] for row in rows}


def _user_version(db_path: Path) -> int:
    with sqlite3.connect(db_path) as cx:
        return cx.execute("PRAGMA user_version").fetchone()[0]


def test_run_migrations_creates_all_tables(tmp_path: Path) -> None:
    """run_migrations() must create all 3 expected tables and set user_version=1."""
    db_path = tmp_path / "test_app.db"
    engine = make_engine(db_path)

    with engine.connect() as conn:
        run_migrations(conn)

    tables = _table_names(db_path)
    assert "connection_profile" in tables, "connection_profile table missing"
    assert "query_history" in tables, "query_history table missing"
    assert "saved_query" in tables, "saved_query table missing"

    version = _user_version(db_path)
    assert version == 1, f"Expected user_version=1, got {version}"


def test_run_migrations_idempotent(tmp_path: Path) -> None:
    """Calling run_migrations() twice must not raise or duplicate tables."""
    db_path = tmp_path / "test_app.db"
    engine = make_engine(db_path)

    with engine.connect() as conn:
        run_migrations(conn)

    with engine.connect() as conn:
        run_migrations(conn)

    assert _user_version(db_path) == 1
    assert len(_table_names(db_path)) == 3
