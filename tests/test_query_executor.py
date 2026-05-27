"""Tests for QueryExecutor — real stack over temp SQLite databases."""

import sqlite3
from pathlib import Path

import pytest

from pydbplay.adapters.base import ReadOnlyViolationError
from pydbplay.core.connection_manager import ConnectionManager
from pydbplay.core.query_executor import (
    MultipleStatementsError,
    QueryError,
    QueryExecutor,
)
from pydbplay.db.repository import Repository, make_engine, run_migrations
from pydbplay.schemas.connection import ConnectionCreate

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_repo(tmp_path: Path) -> Repository:
    """Create a migrated app Repository backed by a tmp SQLite file."""
    db_path = tmp_path / "app.db"
    engine = make_engine(db_path)
    with engine.connect() as conn:
        run_migrations(conn)
    return Repository(engine)


def _seed_target_db(target_path: Path, rows: int = 5) -> None:
    """Create a target SQLite DB with a 'items' table seeded with *rows* rows."""
    with sqlite3.connect(target_path) as cx:
        cx.execute("CREATE TABLE IF NOT EXISTS items (id INTEGER PRIMARY KEY, name TEXT NOT NULL)")
        for i in range(1, rows + 1):
            cx.execute("INSERT INTO items (name) VALUES (?)", (f"item_{i}",))
        cx.commit()


def _register_profile(
    repo: Repository,
    db_path: Path,
    *,
    read_only: bool = False,
) -> int:
    """Insert a sqlite ConnectionProfile and return its id."""
    profile = repo.create_connection(
        ConnectionCreate(
            name="Test SQLite",
            engine="sqlite",
            database=str(db_path),
            read_only=read_only,
        )
    )
    return profile.id


def _make_executor(tmp_path: Path, target_path: Path, *, read_only: bool = False):
    """Build a Repository + ConnectionManager + QueryExecutor triple."""
    repo = _make_repo(tmp_path)
    conn_id = _register_profile(repo, target_path, read_only=read_only)
    cm = ConnectionManager(repo)
    executor = QueryExecutor(cm, repo)
    return executor, conn_id, repo, cm


# ---------------------------------------------------------------------------
# Import / construction smoke test
# ---------------------------------------------------------------------------


def test_query_executor_import() -> None:
    """QueryExecutor must be importable."""
    assert QueryExecutor is not None


# ---------------------------------------------------------------------------
# Basic SELECT
# ---------------------------------------------------------------------------


def test_query_executor_run_select(tmp_path: Path) -> None:
    """QueryExecutor.run() returns rows/columns and records success history."""
    target = tmp_path / "target.db"
    _seed_target_db(target, rows=5)
    executor, conn_id, repo, cm = _make_executor(tmp_path, target)

    try:
        result = executor.run(conn_id, "SELECT * FROM items")

        # Correct data
        assert "id" in result.columns
        assert "name" in result.columns
        assert result.row_count == 5
        assert len(result.rows) == 5
        assert result.duration_ms >= 0

        # History recorded with success
        history = repo.list_history(conn_id)
        assert len(history) >= 1
        last = history[0]
        assert last.success is True
        assert last.row_count == 5
        assert last.sql == "SELECT * FROM items"
        assert last.error_message is None
    finally:
        cm.close_all()


# ---------------------------------------------------------------------------
# Auto-LIMIT
# ---------------------------------------------------------------------------


def test_query_executor_auto_limit(tmp_path: Path) -> None:
    """Auto-LIMIT injects LIMIT 2 and sets limit_applied=True."""
    target = tmp_path / "target.db"
    _seed_target_db(target, rows=5)
    executor, conn_id, _repo, cm = _make_executor(tmp_path, target)

    try:
        result = executor.run(conn_id, "SELECT * FROM items", limit=2)

        assert result.limit_applied is True
        assert "LIMIT" in result.effective_sql.upper()
        assert result.row_count == 2
        assert len(result.rows) == 2
    finally:
        cm.close_all()


def test_query_executor_existing_limit_not_modified(tmp_path: Path) -> None:
    """A SELECT with an explicit LIMIT is NOT modified (limit_applied=False)."""
    target = tmp_path / "target.db"
    _seed_target_db(target, rows=5)
    executor, conn_id, _repo, cm = _make_executor(tmp_path, target)

    try:
        result = executor.run(conn_id, "SELECT * FROM items LIMIT 1", limit=2)

        assert result.limit_applied is False
        # Only 1 row because the user's LIMIT 1 was preserved
        assert result.row_count == 1
    finally:
        cm.close_all()


def test_query_executor_enforce_limit_false_never_modifies(tmp_path: Path) -> None:
    """enforce_limit=False never injects a LIMIT even when none is present."""
    target = tmp_path / "target.db"
    _seed_target_db(target, rows=5)
    executor, conn_id, _repo, cm = _make_executor(tmp_path, target)

    try:
        result = executor.run(conn_id, "SELECT * FROM items", limit=2, enforce_limit=False)

        assert result.limit_applied is False
        # All 5 rows returned because no limit was applied
        assert result.row_count == 5
    finally:
        cm.close_all()


# ---------------------------------------------------------------------------
# Non-SELECT (INSERT)
# ---------------------------------------------------------------------------


def test_query_executor_insert_no_limit(tmp_path: Path) -> None:
    """INSERT executes without LIMIT injection, history is recorded."""
    target = tmp_path / "target.db"
    _seed_target_db(target, rows=5)
    executor, conn_id, repo, cm = _make_executor(tmp_path, target)

    try:
        result = executor.run(conn_id, "INSERT INTO items (name) VALUES ('new_item')")

        assert result.limit_applied is False
        # LIMIT must NOT appear in effective_sql for an INSERT
        assert "LIMIT" not in result.effective_sql.upper()

        # Verify the row was actually inserted
        verify = executor.run(conn_id, "SELECT COUNT(*) as cnt FROM items", enforce_limit=False)
        count_val = verify.rows[0][0]
        assert count_val == 6  # 5 seeded + 1 inserted

        # History recorded
        history = repo.list_history(conn_id)
        insert_records = [h for h in history if "INSERT" in h.sql.upper()]
        assert len(insert_records) >= 1
        assert insert_records[0].success is True
    finally:
        cm.close_all()


# ---------------------------------------------------------------------------
# is_destructive flag
# ---------------------------------------------------------------------------


def test_query_executor_is_destructive_delete(tmp_path: Path) -> None:
    """DELETE is flagged is_destructive=True."""
    target = tmp_path / "target.db"
    _seed_target_db(target, rows=5)
    executor, conn_id, _repo, cm = _make_executor(tmp_path, target)

    try:
        result = executor.run(conn_id, "DELETE FROM items WHERE id = 1")
        assert result.is_destructive is True
    finally:
        cm.close_all()


def test_query_executor_is_destructive_select(tmp_path: Path) -> None:
    """SELECT is flagged is_destructive=False."""
    target = tmp_path / "target.db"
    _seed_target_db(target, rows=5)
    executor, conn_id, _repo, cm = _make_executor(tmp_path, target)

    try:
        result = executor.run(conn_id, "SELECT * FROM items LIMIT 1")
        assert result.is_destructive is False
    finally:
        cm.close_all()


# ---------------------------------------------------------------------------
# DB error path — failure history recorded
# ---------------------------------------------------------------------------


def test_query_executor_db_error_records_failure_history(tmp_path: Path) -> None:
    """A DB error records a failure QueryHistory and raises QueryError."""
    target = tmp_path / "target.db"
    _seed_target_db(target, rows=5)
    executor, conn_id, repo, cm = _make_executor(tmp_path, target)

    try:
        with pytest.raises(QueryError):
            executor.run(conn_id, "SELECT * FROM nonexistent_table")

        # Failure recorded
        history = repo.list_history(conn_id)
        failure_records = [h for h in history if not h.success]
        assert len(failure_records) >= 1
        last_failure = failure_records[0]
        assert last_failure.success is False
        assert last_failure.error_message  # non-empty
        assert "nonexistent_table" in last_failure.sql
    finally:
        cm.close_all()


# ---------------------------------------------------------------------------
# Multi-statement rejection
# ---------------------------------------------------------------------------


def test_query_executor_multi_statement_raises(tmp_path: Path) -> None:
    """Multi-statement SQL raises MultipleStatementsError, nothing is executed."""
    target = tmp_path / "target.db"
    _seed_target_db(target, rows=5)
    executor, conn_id, repo, cm = _make_executor(tmp_path, target)

    try:
        with pytest.raises(MultipleStatementsError):
            executor.run(conn_id, "SELECT 1; SELECT 2")

        # No history should be recorded (execution never reached the DB)
        history = repo.list_history(conn_id)
        assert len(history) == 0
    finally:
        cm.close_all()


# ---------------------------------------------------------------------------
# Read-only connection
# ---------------------------------------------------------------------------


def test_query_executor_read_only_update_raises(tmp_path: Path) -> None:
    """A read-only connection raises on UPDATE and surfaces ReadOnlyViolationError / QueryError."""
    target = tmp_path / "target.db"
    _seed_target_db(target, rows=5)
    executor, conn_id, repo, cm = _make_executor(tmp_path, target, read_only=True)

    try:
        with pytest.raises((QueryError, ReadOnlyViolationError)):
            executor.run(conn_id, "UPDATE items SET name = 'x' WHERE id = 1")

        # Failure should be recorded in history
        history = repo.list_history(conn_id)
        failure_records = [h for h in history if not h.success]
        assert len(failure_records) >= 1
    finally:
        cm.close_all()


def test_query_executor_read_only_select_allowed(tmp_path: Path) -> None:
    """A read-only connection allows SELECT queries."""
    target = tmp_path / "target.db"
    _seed_target_db(target, rows=5)
    executor, conn_id, _repo, cm = _make_executor(tmp_path, target, read_only=True)

    try:
        result = executor.run(conn_id, "SELECT * FROM items")
        assert result.row_count == 5
    finally:
        cm.close_all()


# ---------------------------------------------------------------------------
# history() passthrough
# ---------------------------------------------------------------------------


def test_query_executor_history_passthrough(tmp_path: Path) -> None:
    """history() returns QueryHistory records for the given connection."""
    target = tmp_path / "target.db"
    _seed_target_db(target, rows=3)
    executor, conn_id, _repo, cm = _make_executor(tmp_path, target)

    try:
        executor.run(conn_id, "SELECT * FROM items LIMIT 1")
        executor.run(conn_id, "SELECT * FROM items LIMIT 2")

        records = executor.history(conn_id, limit=10)
        assert len(records) == 2
        # Returned in newest-first order
        assert records[0].sql == "SELECT * FROM items LIMIT 2"
    finally:
        cm.close_all()


# ---------------------------------------------------------------------------
# JSON preservation (CRITICAL fix — no AST round-trip)
# ---------------------------------------------------------------------------


def _seed_json_db(target_path: Path) -> None:
    """Create a target SQLite DB with a 'jt' table that has a JSON text column."""
    with sqlite3.connect(target_path) as cx:
        cx.execute("CREATE TABLE IF NOT EXISTS jt (id INTEGER PRIMARY KEY, data TEXT NOT NULL)")
        cx.execute("INSERT INTO jt (data) VALUES (?)", ('{"name": "alice"}',))
        cx.commit()


def test_query_executor_json_extract_preserved(tmp_path: Path) -> None:
    """json_extract() must NOT be rewritten by sqlglot's AST round-trip.

    Before the fix, _apply_auto_limit regenerated SQL from the AST, which
    turned json_extract(data,'$.name') into data -> '$.name', returning
    '"alice"' (with JSON quotes) instead of 'alice'.
    """
    target = tmp_path / "target.db"
    _seed_json_db(target)
    executor, conn_id, _repo, cm = _make_executor(tmp_path, target)

    try:
        result = executor.run(
            conn_id,
            "SELECT json_extract(data,'$.name') FROM jt",
            limit=10,
        )
        assert result.limit_applied is True
        assert "json_extract" in result.effective_sql
        assert result.rows[0][0] == "alice"
    finally:
        cm.close_all()


# ---------------------------------------------------------------------------
# UNION / INTERSECT / EXCEPT auto-LIMIT (HIGH fix)
# ---------------------------------------------------------------------------


def test_query_executor_union_all_auto_limit(tmp_path: Path) -> None:
    """Auto-LIMIT must apply to a top-level UNION ALL query."""
    target = tmp_path / "target.db"
    _seed_target_db(target, rows=5)
    executor, conn_id, _repo, cm = _make_executor(tmp_path, target)

    try:
        result = executor.run(
            conn_id,
            "SELECT id FROM items UNION ALL SELECT id FROM items",
            limit=3,
        )
        assert result.limit_applied is True
        assert "LIMIT" in result.effective_sql.upper()
        assert result.row_count == 3
    finally:
        cm.close_all()


def test_query_executor_union_existing_limit_not_doubled(tmp_path: Path) -> None:
    """A UNION query that already has a top-level LIMIT must NOT get a second one."""
    target = tmp_path / "target.db"
    _seed_target_db(target, rows=5)
    executor, conn_id, _repo, cm = _make_executor(tmp_path, target)

    try:
        result = executor.run(
            conn_id,
            "SELECT id FROM items UNION ALL SELECT id FROM items LIMIT 5",
            limit=3,
        )
        assert result.limit_applied is False
        # effective_sql must not contain two LIMIT clauses
        upper = result.effective_sql.upper()
        assert upper.count("LIMIT") == 1
    finally:
        cm.close_all()


# ---------------------------------------------------------------------------
# Trailing comment after semicolon not falsely rejected (HIGH fix)
# ---------------------------------------------------------------------------


def test_query_executor_trailing_comment_not_rejected(tmp_path: Path) -> None:
    """SELECT with a trailing comment after ; must NOT be rejected as multi-statement."""
    target = tmp_path / "target.db"
    _seed_target_db(target, rows=5)
    executor, conn_id, _repo, cm = _make_executor(tmp_path, target)

    try:
        result = executor.run(conn_id, "SELECT * FROM items; -- show all", limit=2)
        # Should execute fine and return rows (not raise MultipleStatementsError)
        assert result.row_count == 2
    finally:
        cm.close_all()


def test_query_executor_two_real_statements_still_rejected(tmp_path: Path) -> None:
    """SELECT 1; SELECT 2 must still be rejected as multi-statement."""
    target = tmp_path / "target.db"
    _seed_target_db(target, rows=5)
    executor, conn_id, _repo, cm = _make_executor(tmp_path, target)

    try:
        with pytest.raises(MultipleStatementsError):
            executor.run(conn_id, "SELECT 1; SELECT 2")
    finally:
        cm.close_all()


# ---------------------------------------------------------------------------
# Read-only tightened assertion (tighten existing test's cousin)
# ---------------------------------------------------------------------------


def test_query_executor_read_only_update_raises_query_error_with_cause(
    tmp_path: Path,
) -> None:
    """Read-only UPDATE raises QueryError whose __cause__ is ReadOnlyViolationError."""
    target = tmp_path / "target.db"
    _seed_target_db(target, rows=5)
    executor, conn_id, _repo, cm = _make_executor(tmp_path, target, read_only=True)

    try:
        with pytest.raises(QueryError) as exc_info:
            executor.run(conn_id, "UPDATE items SET name = 'x' WHERE id = 1")
        assert isinstance(exc_info.value.__cause__, ReadOnlyViolationError)
    finally:
        cm.close_all()
