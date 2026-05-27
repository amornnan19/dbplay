"""Manages active DB adapter instances per connection profile."""

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from pydbplay.adapters.base import DBAdapter, UnsupportedEngineError
from pydbplay.adapters.sqlite import SQLiteAdapter
from pydbplay.db.models import ConnectionProfile
from pydbplay.db.repository import Repository
from pydbplay.schemas.connection import ConnectionCreate, ConnectionTestResult


@dataclass
class _Entry:
    """Cache entry holding a live adapter and its last-access timestamp."""

    adapter: DBAdapter
    last_used: float = field(default_factory=time.monotonic)


def _create_adapter(profile: ConnectionProfile) -> DBAdapter:
    """Factory: build an adapter from a persisted ConnectionProfile.

    Args:
        profile: The connection profile to create an adapter for.

    Returns:
        A new DBAdapter instance.

    Raises:
        UnsupportedEngineError: For engines without an implementation yet.
    """
    if profile.engine == "sqlite":
        return SQLiteAdapter(profile.database, read_only=profile.read_only)
    # TODO(phase-pool): tune per-engine pool_size on the adapter's Engine
    # TODO(phase-crypto): decrypt profile.password_encrypted before building DSN
    raise UnsupportedEngineError(
        f"Engine '{profile.engine}' is not yet supported; only 'sqlite' is available"
    )


def _create_adapter_from_create(data: ConnectionCreate) -> DBAdapter:
    """Factory: build a TRANSIENT adapter from a ConnectionCreate schema.

    Used only for test_connection() — the adapter is never cached.

    Args:
        data: The connection create payload.

    Returns:
        A new DBAdapter instance.

    Raises:
        UnsupportedEngineError: For engines without an implementation yet.
    """
    if data.engine == "sqlite":
        return SQLiteAdapter(data.database, read_only=data.read_only)
    # TODO(phase-crypto): decrypt / receive password from ConnectionCreate for pg/mysql DSN
    raise UnsupportedEngineError(
        f"Engine '{data.engine}' is not yet supported; only 'sqlite' is available"
    )


class ConnectionManager:
    """Maintains a cache of DBAdapter instances keyed by connection profile id.

    Adapters are created lazily on first use and evicted after ``idle_timeout``
    seconds of inactivity. Cache mutations are guarded by a threading.Lock
    because FastAPI dispatches sync route handlers in a thread pool.

    Per SPEC §9: the actual DB connection pool lives inside each adapter's
    SQLAlchemy Engine (QueuePool). This class manages the *adapter* cache only.

    Args:
        repository: App-internal Repository for profile look-ups and touch_last_used.
        idle_timeout: Seconds of inactivity before an adapter is evicted (default 300).
        clock: Callable returning a monotonic float; injectable for tests.
    """

    def __init__(
        self,
        repository: Repository,
        *,
        idle_timeout: float = 300.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._repository = repository
        self._idle_timeout = idle_timeout
        self._clock = clock
        self._cache: dict[int, _Entry] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_adapter(self, profile_id: int) -> DBAdapter:
        """Return the cached DBAdapter for *profile_id*, creating it if needed.

        On first access the profile is loaded from the repository, the adapter
        is constructed and cached, and ``repository.touch_last_used`` is called.
        On every subsequent access only ``last_used`` is updated in-memory.

        Args:
            profile_id: Primary key of the ConnectionProfile.

        Returns:
            A live DBAdapter instance.

        Raises:
            KeyError: If no profile with *profile_id* exists in the repository.
            UnsupportedEngineError: If the profile's engine has no adapter yet.
        """
        with self._lock:
            entry = self._cache.get(profile_id)
            if entry is not None:
                entry.last_used = self._clock()
                return entry.adapter

        # Fetch outside the lock to avoid holding it during I/O.
        profile = self._repository.get_connection(profile_id)
        if profile is None:
            raise KeyError(f"No connection profile found with id={profile_id}")

        adapter = _create_adapter(profile)

        with self._lock:
            # Double-check: another thread may have inserted while we were fetching.
            existing = self._cache.get(profile_id)
            if existing is not None:
                # Discard the adapter we just built; use the cached one.
                adapter.dispose()
                existing.last_used = self._clock()
                return existing.adapter

            self._cache[profile_id] = _Entry(adapter=adapter, last_used=self._clock())

        # Touch outside the lock — pure I/O, no cache mutation.
        self._repository.touch_last_used(profile_id)
        return adapter

    def test_connection(self, data: ConnectionCreate) -> ConnectionTestResult:
        """Test a connection from a create payload without caching the adapter.

        The adapter is created, tested, and immediately disposed.

        Args:
            data: ConnectionCreate payload with connection details.

        Returns:
            ConnectionTestResult with ok=True on success, ok=False with a
            descriptive message on failure.
        """
        try:
            adapter = _create_adapter_from_create(data)
        except UnsupportedEngineError:
            return ConnectionTestResult(ok=False, message="engine not yet supported")

        try:
            ok = adapter.test_connection()
            if ok:
                return ConnectionTestResult(ok=True, message="Connection successful")
            return ConnectionTestResult(ok=False, message="Connection test returned False")
        except Exception as exc:
            return ConnectionTestResult(ok=False, message=str(exc))
        finally:
            adapter.dispose()

    def disconnect(self, profile_id: int) -> None:
        """Dispose and remove the cached adapter for *profile_id*.

        No-op if the profile is not currently cached.

        Args:
            profile_id: Primary key of the profile to disconnect.
        """
        with self._lock:
            entry = self._cache.pop(profile_id, None)
        if entry is not None:
            entry.adapter.dispose()

    def evict_idle(self, now: float | None = None) -> int:
        """Dispose and remove adapters that have been idle beyond *idle_timeout*.

        Args:
            now: Reference timestamp (default: ``clock()``). Injected in tests
                 to get deterministic results.

        Returns:
            Number of adapters evicted.
        """
        cutoff = (now if now is not None else self._clock()) - self._idle_timeout
        to_evict: list[tuple[int, DBAdapter]] = []

        with self._lock:
            idle_ids = [
                pid for pid, entry in self._cache.items()
                if entry.last_used < cutoff
            ]
            for pid in idle_ids:
                entry = self._cache.pop(pid)
                to_evict.append((pid, entry.adapter))

        for _pid, adapter in to_evict:
            adapter.dispose()

        return len(to_evict)

    def close_all(self) -> None:
        """Dispose and remove all cached adapters (called on app shutdown)."""
        with self._lock:
            entries = list(self._cache.values())
            self._cache.clear()
        for entry in entries:
            entry.adapter.dispose()
