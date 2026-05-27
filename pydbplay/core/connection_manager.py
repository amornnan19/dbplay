"""Manages active DB adapter instances per connection profile."""

# TODO(phase-1): Implement connection pooling.
# Per SPEC §9: max 5 connections per profile, lazy-open on first query,
# idle timeout 5 minutes.

from pydbplay.adapters.base import DBAdapter
from pydbplay.db.models import ConnectionProfile


class ConnectionManager:
    """Maintains a pool of DBAdapter instances keyed by connection ID.

    Adapters are created lazily on first use and evicted after 5 minutes of
    idle time. Maximum 5 live connections per profile.
    """

    def get_adapter(self, profile: ConnectionProfile) -> DBAdapter:
        """Return a live DBAdapter for *profile*, opening a connection if needed.

        Args:
            profile: The connection profile to connect to.

        Returns:
            A connected DBAdapter instance.

        Raises:
            AdapterError: If the connection cannot be established.
        """
        # TODO(phase-1): implement pool lookup / creation
        raise NotImplementedError

    def close(self, connection_id: int) -> None:
        """Close and evict the adapter for *connection_id*.

        Args:
            connection_id: The profile ID whose adapter should be closed.
        """
        # TODO(phase-1): implement pool eviction
        raise NotImplementedError

    def close_all(self) -> None:
        """Close all active adapters (called on app shutdown)."""
        # TODO(phase-1): iterate pool and close each
        raise NotImplementedError
