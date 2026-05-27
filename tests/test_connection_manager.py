"""Integration tests for ConnectionManager — uses real SQLite files."""

import sqlite3
import threading
from pathlib import Path

import pytest

from pydbplay.adapters.base import DBAdapter, UnsupportedEngineError
from pydbplay.core.connection_manager import ConnectionManager
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


def _seed_target_db(target_path: Path) -> None:
    """Create a minimal target SQLite DB with one user table."""
    with sqlite3.connect(target_path) as cx:
        cx.execute("CREATE TABLE IF NOT EXISTS sample (id INTEGER PRIMARY KEY, name TEXT)")
        cx.execute("INSERT INTO sample (name) VALUES ('Alice')")
        cx.commit()


def _insert_sqlite_profile(repo: Repository, db_path: Path) -> int:
    """Insert a sqlite ConnectionProfile and return its id."""
    profile = repo.create_connection(
        ConnectionCreate(
            name="Test SQLite",
            engine="sqlite",
            database=str(db_path),
        )
    )
    return profile.id


def _insert_mysql_profile(repo: Repository) -> int:
    """Insert a mysql ConnectionProfile (never actually connected) and return its id."""
    profile = repo.create_connection(
        ConnectionCreate(
            name="Test MySQL",
            engine="mysql",
            host="localhost",
            port=3306,
            database="mydb",
            username="admin",
        )
    )
    return profile.id


# ---------------------------------------------------------------------------
# Tests: get_adapter
# ---------------------------------------------------------------------------


def test_get_adapter_returns_working_adapter(tmp_path: Path) -> None:
    """get_adapter returns an adapter that can see tables in the target DB."""
    target = tmp_path / "target.db"
    _seed_target_db(target)
    repo = _make_repo(tmp_path)
    pid = _insert_sqlite_profile(repo, target)

    cm = ConnectionManager(repo)
    adapter = cm.get_adapter(pid)

    tables = adapter.list_tables()
    table_names = {t.name for t in tables}
    assert "sample" in table_names
    cm.close_all()


def test_get_adapter_returns_same_instance(tmp_path: Path) -> None:
    """A second call to get_adapter for the same profile returns the cached instance."""
    target = tmp_path / "target.db"
    _seed_target_db(target)
    repo = _make_repo(tmp_path)
    pid = _insert_sqlite_profile(repo, target)

    cm = ConnectionManager(repo)
    adapter1 = cm.get_adapter(pid)
    adapter2 = cm.get_adapter(pid)

    assert adapter1 is adapter2
    cm.close_all()


def test_get_adapter_touches_last_used(tmp_path: Path) -> None:
    """get_adapter calls repository.touch_last_used so last_used_at is populated."""
    target = tmp_path / "target.db"
    _seed_target_db(target)
    repo = _make_repo(tmp_path)
    pid = _insert_sqlite_profile(repo, target)

    # Verify last_used_at is None before first access.
    profile_before = repo.get_connection(pid)
    assert profile_before is not None
    assert profile_before.last_used_at is None

    cm = ConnectionManager(repo)
    cm.get_adapter(pid)

    profile_after = repo.get_connection(pid)
    assert profile_after is not None
    assert profile_after.last_used_at is not None
    cm.close_all()


def test_get_adapter_missing_profile_raises(tmp_path: Path) -> None:
    """get_adapter raises KeyError for a non-existent profile id."""
    repo = _make_repo(tmp_path)
    cm = ConnectionManager(repo)

    with pytest.raises(KeyError):
        cm.get_adapter(9999)


def test_get_adapter_mysql_raises_unsupported(tmp_path: Path) -> None:
    """get_adapter raises UnsupportedEngineError for a mysql profile."""
    repo = _make_repo(tmp_path)
    pid = _insert_mysql_profile(repo)
    cm = ConnectionManager(repo)

    with pytest.raises(UnsupportedEngineError):
        cm.get_adapter(pid)


# ---------------------------------------------------------------------------
# Tests: test_connection
# ---------------------------------------------------------------------------


def test_test_connection_valid_sqlite(tmp_path: Path) -> None:
    """test_connection returns ok=True for a valid sqlite path."""
    target = tmp_path / "target.db"
    _seed_target_db(target)
    repo = _make_repo(tmp_path)
    cm = ConnectionManager(repo)

    result = cm.test_connection(ConnectionCreate(name="x", engine="sqlite", database=str(target)))
    assert result.ok is True
    assert result.message  # non-empty


def test_test_connection_invalid_sqlite_path(tmp_path: Path) -> None:
    """test_connection returns ok=False for a path inside a non-existent directory."""
    repo = _make_repo(tmp_path)
    cm = ConnectionManager(repo)

    bad_path = str(tmp_path / "nonexistent_dir" / "missing.db")
    result = cm.test_connection(ConnectionCreate(name="x", engine="sqlite", database=bad_path))
    assert result.ok is False
    assert result.message  # non-empty error text


def test_test_connection_mysql_returns_not_supported(tmp_path: Path) -> None:
    """test_connection for mysql returns ok=False with an informative message."""
    repo = _make_repo(tmp_path)
    cm = ConnectionManager(repo)

    result = cm.test_connection(
        ConnectionCreate(
            name="x",
            engine="mysql",
            host="localhost",
            port=3306,
            database="mydb",
            username="admin",
        )
    )
    assert result.ok is False
    assert result.message


def test_test_connection_does_not_cache_adapter(tmp_path: Path) -> None:
    """test_connection must not populate the adapter cache."""
    target = tmp_path / "target.db"
    _seed_target_db(target)
    repo = _make_repo(tmp_path)
    cm = ConnectionManager(repo)

    cm.test_connection(ConnectionCreate(name="x", engine="sqlite", database=str(target)))
    # Cache must remain empty — no profile_id was given
    assert cm._cache == {}


def test_test_connection_adapter_raises_returns_false_with_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """test_connection returns ok=False with the exception text when the adapter raises."""
    from pydbplay.adapters.sqlite import SQLiteAdapter

    target = tmp_path / "target.db"
    _seed_target_db(target)
    repo = _make_repo(tmp_path)
    cm = ConnectionManager(repo)

    dispose_called: list[bool] = []

    original_dispose = SQLiteAdapter.dispose

    def _raising_test_connection(self: SQLiteAdapter) -> bool:
        raise RuntimeError("synthetic driver error")

    def _tracking_dispose(self: SQLiteAdapter) -> None:
        dispose_called.append(True)
        original_dispose(self)

    monkeypatch.setattr(SQLiteAdapter, "test_connection", _raising_test_connection)
    monkeypatch.setattr(SQLiteAdapter, "dispose", _tracking_dispose)

    result = cm.test_connection(ConnectionCreate(name="x", engine="sqlite", database=str(target)))

    assert result.ok is False
    assert "synthetic driver error" in result.message
    # The transient adapter must have been disposed even on the raising path.
    assert dispose_called, "dispose() was not called after adapter.test_connection() raised"


# ---------------------------------------------------------------------------
# Tests: disconnect
# ---------------------------------------------------------------------------


def test_disconnect_removes_cached_adapter(tmp_path: Path) -> None:
    """disconnect removes the adapter; next get_adapter yields a new instance."""
    target = tmp_path / "target.db"
    _seed_target_db(target)
    repo = _make_repo(tmp_path)
    pid = _insert_sqlite_profile(repo, target)

    cm = ConnectionManager(repo)
    adapter1 = cm.get_adapter(pid)
    cm.disconnect(pid)
    adapter2 = cm.get_adapter(pid)

    assert adapter1 is not adapter2
    cm.close_all()


def test_disconnect_noop_when_not_cached(tmp_path: Path) -> None:
    """disconnect on an uncached profile id does not raise."""
    repo = _make_repo(tmp_path)
    cm = ConnectionManager(repo)
    cm.disconnect(999)  # must not raise


# ---------------------------------------------------------------------------
# Tests: evict_idle
# ---------------------------------------------------------------------------


def test_evict_idle_evicts_stale_adapter(tmp_path: Path) -> None:
    """An adapter accessed at t=0 is evicted when now=idle_timeout+1."""
    target = tmp_path / "target.db"
    _seed_target_db(target)
    repo = _make_repo(tmp_path)
    pid = _insert_sqlite_profile(repo, target)

    tick = 0.0

    def clock() -> float:
        return tick

    cm = ConnectionManager(repo, idle_timeout=5.0, clock=clock)
    tick = 0.0
    cm.get_adapter(pid)  # cached at t=0

    # Advance time past idle_timeout
    evicted = cm.evict_idle(now=6.0)
    assert evicted == 1
    assert pid not in cm._cache


def test_evict_idle_spares_fresh_adapter(tmp_path: Path) -> None:
    """An adapter accessed recently is NOT evicted."""
    target = tmp_path / "target.db"
    _seed_target_db(target)
    repo = _make_repo(tmp_path)
    pid = _insert_sqlite_profile(repo, target)

    tick = 100.0

    def clock() -> float:
        return tick

    cm = ConnectionManager(repo, idle_timeout=60.0, clock=clock)
    cm.get_adapter(pid)  # cached at t=100

    evicted = cm.evict_idle(now=150.0)  # only 50 s elapsed, timeout=60 s
    assert evicted == 0
    assert pid in cm._cache
    cm.close_all()


def test_evict_idle_returns_count(tmp_path: Path) -> None:
    """evict_idle return value equals the number of evicted entries."""
    target1 = tmp_path / "t1.db"
    target2 = tmp_path / "t2.db"
    _seed_target_db(target1)
    _seed_target_db(target2)
    repo = _make_repo(tmp_path)
    pid1 = _insert_sqlite_profile(repo, target1)
    pid2 = _insert_sqlite_profile(repo, target2)

    tick = 0.0

    def clock() -> float:
        return tick

    cm = ConnectionManager(repo, idle_timeout=10.0, clock=clock)
    cm.get_adapter(pid1)
    cm.get_adapter(pid2)

    evicted = cm.evict_idle(now=20.0)
    assert evicted == 2
    assert len(cm._cache) == 0


# ---------------------------------------------------------------------------
# Tests: close_all
# ---------------------------------------------------------------------------


def test_close_all_empties_cache(tmp_path: Path) -> None:
    """close_all disposes all adapters and leaves the cache empty."""
    target1 = tmp_path / "t1.db"
    target2 = tmp_path / "t2.db"
    _seed_target_db(target1)
    _seed_target_db(target2)
    repo = _make_repo(tmp_path)
    pid1 = _insert_sqlite_profile(repo, target1)
    pid2 = _insert_sqlite_profile(repo, target2)

    cm = ConnectionManager(repo)
    cm.get_adapter(pid1)
    cm.get_adapter(pid2)
    assert len(cm._cache) == 2

    cm.close_all()
    assert len(cm._cache) == 0


# ---------------------------------------------------------------------------
# Tests: thread-safety smoke test
# ---------------------------------------------------------------------------


def test_concurrent_get_adapter_single_instance(tmp_path: Path) -> None:
    """Concurrent get_adapter calls from multiple threads yield ONE cached instance
    and every race-loser's adapter is disposed exactly once.

    A Barrier is used to ensure all N threads build their adapters before any
    one of them acquires the double-check lock, guaranteeing N-1 race-losers
    are created and then disposed by the manager.
    """
    from pydbplay.adapters.sqlite import SQLiteAdapter
    from pydbplay.core import connection_manager as _cm_module

    target = tmp_path / "target.db"
    _seed_target_db(target)
    repo = _make_repo(tmp_path)
    pid = _insert_sqlite_profile(repo, target)

    n_threads = 8
    barrier = threading.Barrier(n_threads)

    # Wrap the real _create_adapter so every thread blocks at the barrier
    # *after* building its adapter but *before* entering the double-check lock.
    original_create = _cm_module._create_adapter

    def _barrier_create(profile: object) -> DBAdapter:
        adapter = original_create(profile)  # type: ignore[arg-type]
        barrier.wait()  # all N threads synchronise here
        return adapter

    dispose_lock = threading.Lock()
    dispose_count: list[int] = [0]

    original_dispose = SQLiteAdapter.dispose

    def _counting_dispose(self: SQLiteAdapter) -> None:
        with dispose_lock:
            dispose_count[0] += 1
        original_dispose(self)

    cm = ConnectionManager(repo)
    collected: list[int] = []
    errors: list[Exception] = []

    _cm_module._create_adapter = _barrier_create  # type: ignore[assignment]
    SQLiteAdapter.dispose = _counting_dispose  # type: ignore[method-assign]

    try:

        def worker() -> None:
            try:
                adapter = cm.get_adapter(pid)
                collected.append(id(adapter))
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
    finally:
        _cm_module._create_adapter = original_create  # type: ignore[assignment]
        SQLiteAdapter.dispose = original_dispose  # type: ignore[method-assign]

    assert not errors
    # All threads must have received the same adapter instance.
    assert len(set(collected)) == 1
    # Exactly one adapter survives in the cache; all N-1 race-losers were disposed.
    assert len(cm._cache) == 1
    assert dispose_count[0] == n_threads - 1, (
        f"Expected {n_threads - 1} dispose() calls for race-losers, got {dispose_count[0]}"
    )
    cm.close_all()
