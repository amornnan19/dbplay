"""Round-trip tests for the Repository class (CRUD, ordering, visibility rules).

Each test uses a fresh tmp_path SQLite file so there is no shared state.
Migrations are run via run_migrations() before any Repository call.
"""

import sqlite3
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from pydbplay.db.models import QueryHistory
from pydbplay.db.repository import Repository, make_engine, run_migrations
from pydbplay.schemas.connection import ConnectionCreate, ConnectionUpdate, SavedQueryUpdate

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _table_names(db_path: Path) -> set[str]:
    """Return all user-created table names in the SQLite file."""
    with sqlite3.connect(db_path) as cx:
        rows = cx.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
    return {row[0] for row in rows}


def _user_version(db_path: Path) -> int:
    with sqlite3.connect(db_path) as cx:
        return cx.execute("PRAGMA user_version").fetchone()[0]


def _make_repo(tmp_path: Path) -> tuple[Repository, Path]:
    """Create a migrated Repository backed by a tmp SQLite file."""
    db_path = tmp_path / "test_app.db"
    engine = make_engine(db_path)
    with engine.connect() as conn:
        run_migrations(conn)
    return Repository(engine), db_path


def _sample_create(**overrides: object) -> ConnectionCreate:
    """Return a minimal ConnectionCreate, with optional field overrides."""
    defaults: dict[str, object] = {
        "name": "Local PG",
        "engine": "postgres",
        "host": "localhost",
        "port": 5432,
        "database": "mydb",
        "username": "admin",
        "password": "s3cret",
        "ssl_mode": None,
        "read_only": False,
        "color": "#6366f1",
    }
    defaults.update(overrides)
    return ConnectionCreate(**defaults)  # type: ignore[arg-type]


def _sample_history(
    connection_id: int, *, sql: str = "SELECT 1", offset_seconds: int = 0
) -> QueryHistory:
    """Return a minimal QueryHistory (id=0 — ignored by add_history)."""
    return QueryHistory(
        id=0,
        connection_id=connection_id,
        sql=sql,
        executed_at=datetime.now(UTC).replace(tzinfo=None) + timedelta(seconds=offset_seconds),
        duration_ms=10,
        row_count=1,
        success=True,
        error_message=None,
    )


# ---------------------------------------------------------------------------
# Existing migration tests (kept)
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# ConnectionProfile CRUD
# ---------------------------------------------------------------------------


def test_create_connection_returns_full_model(tmp_path: Path) -> None:
    repo, _ = _make_repo(tmp_path)
    data = _sample_create()

    profile = repo.create_connection(data)

    assert profile.id > 0
    assert profile.name == "Local PG"
    assert profile.engine == "postgres"
    assert profile.host == "localhost"
    assert profile.port == 5432
    assert profile.database == "mydb"
    assert profile.username == "admin"
    assert profile.password_encrypted == "s3cret"
    assert profile.ssl_mode is None
    assert profile.read_only is False
    assert profile.color == "#6366f1"
    assert profile.last_used_at is None
    assert isinstance(profile.created_at, datetime)
    assert isinstance(profile.updated_at, datetime)


def test_create_connection_read_only_round_trips(tmp_path: Path) -> None:
    repo, _ = _make_repo(tmp_path)
    data = _sample_create(read_only=True)

    profile = repo.create_connection(data)

    assert profile.read_only is True
    fetched = repo.get_connection(profile.id)
    assert fetched is not None
    assert fetched.read_only is True


def test_get_connection_returns_none_for_missing(tmp_path: Path) -> None:
    repo, _ = _make_repo(tmp_path)

    result = repo.get_connection(9999)

    assert result is None


def test_get_connection_returns_correct_row(tmp_path: Path) -> None:
    repo, _ = _make_repo(tmp_path)
    profile = repo.create_connection(_sample_create(name="Alpha"))

    fetched = repo.get_connection(profile.id)

    assert fetched is not None
    assert fetched.id == profile.id
    assert fetched.name == "Alpha"


def test_list_connections_empty(tmp_path: Path) -> None:
    repo, _ = _make_repo(tmp_path)

    result = repo.list_connections()

    assert result == []


def test_list_connections_returns_all(tmp_path: Path) -> None:
    repo, _ = _make_repo(tmp_path)
    repo.create_connection(_sample_create(name="A"))
    repo.create_connection(_sample_create(name="B"))
    repo.create_connection(_sample_create(name="C"))

    result = repo.list_connections()

    assert len(result) == 3
    names = {p.name for p in result}
    assert names == {"A", "B", "C"}


def test_update_connection_partial_only_changed_fields(tmp_path: Path) -> None:
    repo, _ = _make_repo(tmp_path)
    profile = repo.create_connection(_sample_create(name="Original", host="host1"))
    original_updated_at = profile.updated_at

    # Small sleep to ensure updated_at advances (datetime resolution)
    time.sleep(0.01)
    updated = repo.update_connection(profile.id, ConnectionUpdate(name="Renamed"))

    assert updated is not None
    assert updated.name == "Renamed"
    # Unchanged fields stay the same
    assert updated.host == "host1"
    assert updated.engine == profile.engine
    assert updated.database == profile.database
    # updated_at must advance
    assert updated.updated_at > original_updated_at


def test_update_connection_password_stored_as_encrypted(tmp_path: Path) -> None:
    repo, _ = _make_repo(tmp_path)
    profile = repo.create_connection(_sample_create())

    updated = repo.update_connection(profile.id, ConnectionUpdate(password="newpass"))

    assert updated is not None
    assert updated.password_encrypted == "newpass"


def test_update_connection_returns_none_for_missing(tmp_path: Path) -> None:
    repo, _ = _make_repo(tmp_path)

    result = repo.update_connection(9999, ConnectionUpdate(name="Ghost"))

    assert result is None


def test_delete_connection_returns_true(tmp_path: Path) -> None:
    repo, _ = _make_repo(tmp_path)
    profile = repo.create_connection(_sample_create())

    deleted = repo.delete_connection(profile.id)

    assert deleted is True
    assert repo.get_connection(profile.id) is None


def test_delete_connection_returns_false_for_missing(tmp_path: Path) -> None:
    repo, _ = _make_repo(tmp_path)

    deleted = repo.delete_connection(9999)

    assert deleted is False


def test_delete_connection_gone_from_list(tmp_path: Path) -> None:
    repo, _ = _make_repo(tmp_path)
    p1 = repo.create_connection(_sample_create(name="Keep"))
    p2 = repo.create_connection(_sample_create(name="Remove"))

    repo.delete_connection(p2.id)
    remaining = repo.list_connections()

    ids = {p.id for p in remaining}
    assert p1.id in ids
    assert p2.id not in ids


# ---------------------------------------------------------------------------
# touch_last_used + list ordering
# ---------------------------------------------------------------------------


def test_touch_last_used_sets_timestamp(tmp_path: Path) -> None:
    repo, _ = _make_repo(tmp_path)
    profile = repo.create_connection(_sample_create())
    assert profile.last_used_at is None

    repo.touch_last_used(profile.id)

    refreshed = repo.get_connection(profile.id)
    assert refreshed is not None
    assert refreshed.last_used_at is not None
    assert isinstance(refreshed.last_used_at, datetime)


def test_list_connections_ordering_by_last_used(tmp_path: Path) -> None:
    """Connections with a recent last_used_at appear before NULLs and older ones."""
    repo, _ = _make_repo(tmp_path)
    repo.create_connection(_sample_create(name="Never"))
    p_old = repo.create_connection(_sample_create(name="Old"))
    p_new = repo.create_connection(_sample_create(name="New"))

    # Touch old first, then new
    time.sleep(0.01)
    repo.touch_last_used(p_old.id)
    time.sleep(0.01)
    repo.touch_last_used(p_new.id)

    ordered = repo.list_connections()
    names = [p.name for p in ordered]

    # "New" should be first (most recent last_used_at)
    # "Old" should be second
    # "Never" should be last (NULL last_used_at)
    assert names.index("New") < names.index("Old")
    assert names.index("Old") < names.index("Never")


# ---------------------------------------------------------------------------
# QueryHistory
# ---------------------------------------------------------------------------


def test_add_history_returns_with_id(tmp_path: Path) -> None:
    repo, _ = _make_repo(tmp_path)
    profile = repo.create_connection(_sample_create())
    entry = _sample_history(profile.id, sql="SELECT 42")

    saved = repo.add_history(entry)

    assert saved.id > 0
    assert saved.sql == "SELECT 42"
    assert saved.connection_id == profile.id


def test_list_history_most_recent_first(tmp_path: Path) -> None:
    repo, _ = _make_repo(tmp_path)
    profile = repo.create_connection(_sample_create())

    # Insert 3 entries with increasing executed_at offsets
    repo.add_history(_sample_history(profile.id, sql="SELECT 1", offset_seconds=0))
    repo.add_history(_sample_history(profile.id, sql="SELECT 2", offset_seconds=1))
    repo.add_history(_sample_history(profile.id, sql="SELECT 3", offset_seconds=2))

    results = repo.list_history(profile.id)

    sqls = [r.sql for r in results]
    assert sqls == ["SELECT 3", "SELECT 2", "SELECT 1"]


def test_list_history_respects_limit(tmp_path: Path) -> None:
    repo, _ = _make_repo(tmp_path)
    profile = repo.create_connection(_sample_create())

    for i in range(10):
        repo.add_history(_sample_history(profile.id, sql=f"SELECT {i}", offset_seconds=i))

    results = repo.list_history(profile.id, limit=3)

    assert len(results) == 3
    # Most recent 3 — offsets 9, 8, 7
    sqls = [r.sql for r in results]
    assert sqls == ["SELECT 9", "SELECT 8", "SELECT 7"]


def test_list_history_default_limit_is_50(tmp_path: Path) -> None:
    repo, _ = _make_repo(tmp_path)
    profile = repo.create_connection(_sample_create())

    for i in range(60):
        repo.add_history(_sample_history(profile.id, sql=f"SELECT {i}", offset_seconds=i))

    results = repo.list_history(profile.id)

    assert len(results) == 50


def test_list_history_filtered_by_connection(tmp_path: Path) -> None:
    repo, _ = _make_repo(tmp_path)
    p1 = repo.create_connection(_sample_create(name="C1"))
    p2 = repo.create_connection(_sample_create(name="C2"))

    repo.add_history(_sample_history(p1.id, sql="FROM C1"))
    repo.add_history(_sample_history(p2.id, sql="FROM C2"))

    c1_history = repo.list_history(p1.id)

    assert len(c1_history) == 1
    assert c1_history[0].sql == "FROM C1"


# ---------------------------------------------------------------------------
# SavedQuery
# ---------------------------------------------------------------------------


def test_create_saved_query_global(tmp_path: Path) -> None:
    repo, _ = _make_repo(tmp_path)

    sq = repo.create_saved_query(name="Count all", sql="SELECT COUNT(*) FROM users")

    assert sq.id > 0
    assert sq.connection_id is None
    assert sq.name == "Count all"
    assert sq.sql == "SELECT COUNT(*) FROM users"


def test_create_saved_query_per_connection(tmp_path: Path) -> None:
    repo, _ = _make_repo(tmp_path)
    profile = repo.create_connection(_sample_create())

    sq = repo.create_saved_query(
        name="Show active",
        sql="SELECT * FROM users WHERE active=1",
        connection_id=profile.id,
    )

    assert sq.connection_id == profile.id


def test_get_saved_query_returns_none_for_missing(tmp_path: Path) -> None:
    repo, _ = _make_repo(tmp_path)

    assert repo.get_saved_query(9999) is None


def test_get_saved_query_round_trip(tmp_path: Path) -> None:
    repo, _ = _make_repo(tmp_path)
    sq = repo.create_saved_query(name="My query", sql="SELECT 1")

    fetched = repo.get_saved_query(sq.id)

    assert fetched is not None
    assert fetched.id == sq.id
    assert fetched.name == "My query"


def test_list_saved_queries_global_only_when_no_connection_id(tmp_path: Path) -> None:
    """list_saved_queries(None) returns only global queries."""
    repo, _ = _make_repo(tmp_path)
    profile = repo.create_connection(_sample_create())

    repo.create_saved_query(name="Global", sql="SELECT 1")  # no connection_id
    repo.create_saved_query(name="Specific", sql="SELECT 2", connection_id=profile.id)

    globals_only = repo.list_saved_queries(connection_id=None)

    names = [sq.name for sq in globals_only]
    assert "Global" in names
    assert "Specific" not in names


def test_list_saved_queries_connection_includes_globals(tmp_path: Path) -> None:
    """list_saved_queries(conn_id) returns that connection's AND global queries."""
    repo, _ = _make_repo(tmp_path)
    p1 = repo.create_connection(_sample_create(name="P1"))
    p2 = repo.create_connection(_sample_create(name="P2"))

    repo.create_saved_query(name="Global", sql="SELECT 1")
    repo.create_saved_query(name="ForP1", sql="SELECT 2", connection_id=p1.id)
    repo.create_saved_query(name="ForP2", sql="SELECT 3", connection_id=p2.id)

    p1_queries = repo.list_saved_queries(connection_id=p1.id)
    names = [sq.name for sq in p1_queries]

    assert "Global" in names
    assert "ForP1" in names
    assert "ForP2" not in names


def test_update_saved_query_partial(tmp_path: Path) -> None:
    repo, _ = _make_repo(tmp_path)
    sq = repo.create_saved_query(name="Old name", sql="SELECT 1", description="old desc")
    original_updated_at = sq.updated_at

    time.sleep(0.01)
    updated = repo.update_saved_query(sq.id, SavedQueryUpdate(name="New name"))

    assert updated is not None
    assert updated.name == "New name"
    assert updated.sql == "SELECT 1"  # unchanged
    assert updated.description == "old desc"  # unchanged
    assert updated.updated_at > original_updated_at


def test_update_saved_query_returns_none_for_missing(tmp_path: Path) -> None:
    repo, _ = _make_repo(tmp_path)

    result = repo.update_saved_query(9999, SavedQueryUpdate(name="Ghost"))

    assert result is None


def test_delete_saved_query_returns_true(tmp_path: Path) -> None:
    repo, _ = _make_repo(tmp_path)
    sq = repo.create_saved_query(name="To delete", sql="SELECT 1")

    deleted = repo.delete_saved_query(sq.id)

    assert deleted is True
    assert repo.get_saved_query(sq.id) is None


def test_delete_saved_query_returns_false_for_missing(tmp_path: Path) -> None:
    repo, _ = _make_repo(tmp_path)

    deleted = repo.delete_saved_query(9999)

    assert deleted is False


# ---------------------------------------------------------------------------
# FK enforcement regression tests (Issue: PRAGMA foreign_keys not set)
# ---------------------------------------------------------------------------


def test_delete_connection_cascades_query_history(tmp_path: Path) -> None:
    """Deleting a connection must cascade-delete its query_history rows."""
    repo, _ = _make_repo(tmp_path)
    profile = repo.create_connection(_sample_create())
    repo.add_history(_sample_history(profile.id, sql="SELECT cascade"))

    assert len(repo.list_history(profile.id)) == 1

    repo.delete_connection(profile.id)

    # History for the deleted connection must be gone
    assert repo.list_history(profile.id) == []


def test_delete_connection_nullifies_saved_query_connection_id(tmp_path: Path) -> None:
    """Deleting a connection must set saved_query.connection_id to NULL (ON DELETE SET NULL)."""
    repo, _ = _make_repo(tmp_path)
    profile = repo.create_connection(_sample_create())
    sq = repo.create_saved_query(name="Linked", sql="SELECT 1", connection_id=profile.id)
    assert sq.connection_id == profile.id

    repo.delete_connection(profile.id)

    fetched = repo.get_saved_query(sq.id)
    assert fetched is not None, "saved_query row must survive the connection deletion"
    assert fetched.connection_id is None, "connection_id must be set to NULL (not orphaned)"


# ---------------------------------------------------------------------------
# update_saved_query — description clearable to NULL
# ---------------------------------------------------------------------------


def test_update_saved_query_clears_description_to_none(tmp_path: Path) -> None:
    """Explicitly passing description=None must null out the column."""
    repo, _ = _make_repo(tmp_path)
    sq = repo.create_saved_query(name="Q", sql="SELECT 1", description="initial")
    assert sq.description == "initial"

    updated = repo.update_saved_query(sq.id, SavedQueryUpdate(description=None))

    assert updated is not None
    assert updated.description is None


def test_update_saved_query_absent_field_unchanged(tmp_path: Path) -> None:
    """Fields not present in SavedQueryUpdate payload must remain unchanged."""
    repo, _ = _make_repo(tmp_path)
    sq = repo.create_saved_query(name="Original", sql="SELECT 1", description="keep me")

    updated = repo.update_saved_query(sq.id, SavedQueryUpdate(name="Renamed"))

    assert updated is not None
    assert updated.name == "Renamed"
    assert updated.sql == "SELECT 1"  # not in payload — unchanged
    assert updated.description == "keep me"  # not in payload — unchanged


# ---------------------------------------------------------------------------
# list_connections tie-break by created_at DESC (both last_used_at NULL)
# ---------------------------------------------------------------------------


def test_list_connections_null_last_used_tiebreak_by_created_at(tmp_path: Path) -> None:
    """Two connections with NULL last_used_at must be ordered by created_at DESC."""
    repo, _ = _make_repo(tmp_path)
    # Insert with a small delay so created_at values are distinct
    first = repo.create_connection(_sample_create(name="First"))
    time.sleep(0.02)
    second = repo.create_connection(_sample_create(name="Second"))

    # Neither is ever used — both have NULL last_used_at
    assert first.last_used_at is None
    assert second.last_used_at is None

    ordered = repo.list_connections()
    names = [p.name for p in ordered]

    # Most recently created (Second) must appear before the earlier one (First)
    assert names.index("Second") < names.index("First")
