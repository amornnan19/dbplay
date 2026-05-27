"""Query history recording and retrieval."""

# TODO(phase-1): Implement query history persistence via db/repository.py

from pydbplay.db.models import QueryHistory


class HistoryManager:
    """Records executed queries and their outcomes in the app-internal SQLite DB."""

    def record(
        self,
        connection_id: int,
        sql: str,
        duration_ms: int,
        row_count: int | None,
        success: bool,
        error_message: str | None = None,
    ) -> None:
        """Persist a query execution record.

        Args:
            connection_id: ID of the connection profile used.
            sql: The SQL that was executed.
            duration_ms: Wall-clock execution time in milliseconds.
            row_count: Number of rows returned/affected (None if unknown).
            success: True if the query completed without error.
            error_message: Error description if success=False.
        """
        # TODO(phase-1): insert into query_history via Repository
        raise NotImplementedError

    def get_history(
        self,
        connection_id: int,
        *,
        limit: int = 50,
    ) -> list[QueryHistory]:
        """Return recent query history for a connection.

        Args:
            connection_id: Connection profile ID to filter by.
            limit: Maximum number of records to return (most recent first).

        Returns:
            List of QueryHistory records, newest first.
        """
        # TODO(phase-1): query via Repository
        raise NotImplementedError
