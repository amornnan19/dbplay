"""Executes SQL statements against a DBAdapter with safety guards."""

# TODO(phase-1): Implement query execution with:
#   - auto-LIMIT injection (default 1000) when no LIMIT present
#   - destructive-query guard (confirm dialog via is_destructive flag)
#   - multi-statement rejection (SPEC §9)
#   - history recording

from pydbplay.adapters.base import DBAdapter
from pydbplay.schemas.query import QueryResult


class QueryExecutor:
    """Wraps a DBAdapter and applies app-level query policies.

    Policies applied (per SPEC §9):
    - Auto-append ``LIMIT 1000`` when the query has no LIMIT clause.
    - Reject multi-statement SQL (single statement per run).
    - Record query history on success and failure.
    """

    def __init__(self, adapter: DBAdapter) -> None:
        self._adapter = adapter

    def run(
        self,
        sql: str,
        *,
        limit: int = 1000,
        skip_limit: bool = False,
    ) -> QueryResult:
        """Execute *sql* and return results.

        Args:
            sql: The SQL statement to execute.
            limit: Auto-injected LIMIT when the query has none (default 1000).
            skip_limit: If True, do NOT inject LIMIT (user confirmed large result).

        Returns:
            A QueryResult with columns, rows, duration_ms, and row_count.

        Raises:
            AdapterError: On DB-level errors.
            ReadOnlyViolation: When profile is read-only and sql is non-SELECT.
        """
        # TODO(phase-1): implement
        raise NotImplementedError
