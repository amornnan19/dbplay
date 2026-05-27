"""Export query results to CSV, JSON, or SQL INSERT statements."""

# TODO(phase-3): Implement streaming export.
# Per SPEC §9: use execute_stream (chunked cursor) — never fetchall() on large tables.

from collections.abc import Generator

from pydbplay.adapters.base import DBAdapter


class Exporter:
    """Streams query results to CSV, JSON, or SQL INSERT format.

    Uses ``DBAdapter.execute_stream`` (server-side cursor, chunk_size=1000)
    so large tables do not exhaust memory.
    """

    def __init__(self, adapter: DBAdapter) -> None:
        self._adapter = adapter

    def export_csv(self, sql: str, *, chunk_size: int = 1000) -> Generator[str, None, None]:
        """Stream query results as CSV rows.

        Args:
            sql: SELECT statement to export.
            chunk_size: Number of rows fetched per cursor batch.

        Yields:
            CSV-formatted lines (header + data rows).
        """
        # TODO(phase-3): implement
        raise NotImplementedError

    def export_json(self, sql: str, *, chunk_size: int = 1000) -> Generator[str, None, None]:
        """Stream query results as a JSON array (newline-delimited objects).

        Args:
            sql: SELECT statement to export.
            chunk_size: Number of rows fetched per cursor batch.

        Yields:
            JSON object strings, one per row.
        """
        # TODO(phase-3): implement
        raise NotImplementedError

    def export_sql_inserts(
        self,
        table: str,
        sql: str,
        *,
        chunk_size: int = 1000,
    ) -> Generator[str, None, None]:
        """Stream query results as SQL INSERT statements.

        Args:
            table: Target table name for the INSERT statements.
            sql: SELECT statement to export.
            chunk_size: Number of rows fetched per cursor batch.

        Yields:
            SQL INSERT statement strings.
        """
        # TODO(phase-3): implement
        raise NotImplementedError
