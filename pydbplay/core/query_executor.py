"""Executes SQL statements against a DBAdapter with safety guards (SPEC §9)."""

import re
import time
from datetime import UTC, datetime

import sqlglot
import sqlglot.expressions as exp

from pydbplay.adapters.base import DBAdapter
from pydbplay.core.connection_manager import ConnectionManager
from pydbplay.core.sql_validator import validate
from pydbplay.db.models import QueryHistory
from pydbplay.db.repository import Repository
from pydbplay.schemas.query import QueryRunResult

# ---------------------------------------------------------------------------
# Error types
# ---------------------------------------------------------------------------


class QueryError(Exception):
    """Raised by QueryExecutor when a query cannot be executed.

    This wraps underlying adapter/DB errors so callers get a single typed
    exception from the executor layer.  The original exception is chained via
    ``__cause__``.
    """


class MultipleStatementsError(QueryError):
    """Raised when the SQL contains more than one statement (SPEC §9)."""


# ---------------------------------------------------------------------------
# QueryExecutor
# ---------------------------------------------------------------------------


def _now_utc() -> datetime:
    """Return the current UTC datetime (timezone-naive for SQLite storage)."""
    return datetime.now(UTC).replace(tzinfo=None)


def _apply_auto_limit(sql: str, dialect: str, limit: int) -> tuple[str, bool]:
    """Inject a LIMIT clause into *sql* if it is a top-level SELECT without one.

    Rules (SPEC §9):
    - Only top-level SELECT / UNION / INTERSECT / EXCEPT statements are modified.
    - If the query already contains a top-level LIMIT, it is left unchanged.
    - INSERT / UPDATE / DELETE / DDL are never modified.
    - If sqlglot cannot parse the SQL, the original string is returned unmodified
      (let the DB report any syntax error).
    - The LIMIT is appended textually to the ORIGINAL SQL string — sqlglot is
      used only to decide eligibility.  This prevents AST round-trip from
      transforming dialect-specific syntax (e.g. json_extract → -> operator).

    Args:
        sql: The original SQL string.
        dialect: sqlglot dialect name for parsing only (never for generation).
        limit: The LIMIT value to inject.

    Returns:
        A ``(effective_sql, limit_applied)`` tuple.  ``limit_applied`` is True
        only when the LIMIT clause was injected by this function.
    """
    effective_dialect: str | None = dialect if dialect else None
    try:
        parsed = sqlglot.parse(sql, dialect=effective_dialect)
    except sqlglot.errors.ParseError:
        # Cannot parse → return original; the DB will surface the syntax error.
        return sql, False

    # Filter out None entries and bare Semicolon nodes (trailing semicolons).
    statements = [s for s in parsed if s is not None and not isinstance(s, exp.Semicolon)]

    if not statements:
        return sql, False

    # More than one real statement → not eligible (caller's multi-statement guard
    # will reject it separately; we just leave SQL unchanged here).
    if len(statements) > 1:
        return sql, False

    stmt = statements[0]

    # Only touch SELECT / UNION / INTERSECT / EXCEPT at the outer level.
    if not isinstance(stmt, (exp.Select, exp.Union, exp.Intersect, exp.Except)):
        return sql, False

    # If there is already a top-level LIMIT clause, do not modify.
    if stmt.args.get("limit") is not None:
        return sql, False

    # Append the LIMIT textually to the ORIGINAL string so dialect-specific
    # syntax is preserved verbatim.  Strip any trailing semicolon (even when
    # followed by whitespace / line-comments like `; -- foo`) so the DB does
    # not see two statements, then append on a new line so a trailing line
    # comment does not swallow the LIMIT keyword.
    #
    # Pattern: optional whitespace, then `;`, then only whitespace/line-comments
    # to end-of-string.  Replace the semicolon and everything after with "".
    stripped = re.sub(r";\s*(--[^\n]*)?\s*$", "", sql.rstrip())
    effective_sql = stripped + f"\nLIMIT {limit}"

    return effective_sql, True


class QueryExecutor:
    """Wraps ConnectionManager and Repository; applies app-level query policies.

    Per SPEC §9:
    - One statement per run — multi-statement SQL is rejected before execution.
    - Auto-inject ``LIMIT <limit>`` for top-level SELECT without an existing LIMIT.
    - Record query history on both success and failure paths.

    Args:
        connection_manager: Provides live DBAdapter instances.
        repository: Persists QueryHistory records.
    """

    def __init__(
        self,
        connection_manager: ConnectionManager,
        repository: Repository,
    ) -> None:
        self._connection_manager = connection_manager
        self._repository = repository

    # ------------------------------------------------------------------
    # Primary execution method
    # ------------------------------------------------------------------

    def run(
        self,
        conn_id: int,
        sql: str,
        *,
        limit: int = 1000,
        enforce_limit: bool = True,
    ) -> QueryRunResult:
        """Execute *sql* against the connection identified by *conn_id*.

        Args:
            conn_id: Primary key of the ConnectionProfile.
            sql: The SQL statement to execute (original, as typed by the user).
            limit: Auto-injected LIMIT value for SELECT queries (default 1000).
            enforce_limit: When False, never inject a LIMIT clause.

        Returns:
            QueryRunResult with columns, rows, timing, effective SQL, and flags.

        Raises:
            KeyError: If *conn_id* does not exist in the repository.
            UnsupportedEngineError: If the profile's engine has no adapter.
            MultipleStatementsError: If *sql* contains more than one statement.
            QueryError: Wrapping any adapter/DB error raised during execution.
                The failure is also recorded in QueryHistory before re-raising.
        """
        # Step 1: Resolve adapter (let KeyError / UnsupportedEngineError propagate).
        adapter: DBAdapter = self._connection_manager.get_adapter(conn_id)

        # Step 2: One-statement guard.
        self._reject_multi_statement(sql, adapter.dialect)

        # Step 3: Determine is_destructive from the original SQL.
        validation = validate(sql, dialect=adapter.dialect)
        is_destructive = validation.is_destructive

        # Step 4: Auto-LIMIT injection.
        if enforce_limit:
            effective_sql, limit_applied = _apply_auto_limit(sql, adapter.dialect, limit)
            truncated_possible = limit_applied
        else:
            effective_sql = sql
            limit_applied = False
            truncated_possible = False

        # Step 5: Execute and record history.
        start = time.perf_counter()
        try:
            result = adapter.execute(effective_sql)
            duration_ms = int((time.perf_counter() - start) * 1000)
        except Exception as exc:
            duration_ms = int((time.perf_counter() - start) * 1000)
            self._record_history(
                conn_id=conn_id,
                sql=sql,
                duration_ms=duration_ms,
                row_count=None,
                success=False,
                error_message=str(exc),
            )
            raise QueryError(str(exc)) from exc

        # Step 6: Record success history.
        self._record_history(
            conn_id=conn_id,
            sql=sql,
            duration_ms=duration_ms,
            row_count=result.row_count,
            success=True,
            error_message=None,
        )

        return QueryRunResult(
            columns=result.columns,
            rows=result.rows,
            row_count=result.row_count,
            duration_ms=duration_ms,
            effective_sql=effective_sql,
            limit_applied=limit_applied,
            truncated_possible=truncated_possible,
            is_destructive=is_destructive,
        )

    # ------------------------------------------------------------------
    # History passthrough
    # ------------------------------------------------------------------

    def history(self, conn_id: int, limit: int = 50) -> list[QueryHistory]:
        """Return recent query history for *conn_id*.

        Args:
            conn_id: Connection profile to filter on.
            limit: Maximum number of records to return (newest first).

        Returns:
            List of QueryHistory records.
        """
        return self._repository.list_history(conn_id, limit=limit)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _reject_multi_statement(self, sql: str, dialect: str) -> None:
        """Raise MultipleStatementsError if *sql* contains more than one statement.

        Uses sqlglot to parse; if parsing fails the guard is skipped (the DB
        will surface the syntax error on execution).
        """
        effective_dialect: str | None = dialect if dialect else None
        try:
            parsed = sqlglot.parse(sql, dialect=effective_dialect)
        except sqlglot.errors.ParseError:
            # Can't parse → let the DB report the error.
            return

        non_empty = [s for s in parsed if s is not None and not isinstance(s, exp.Semicolon)]
        if len(non_empty) > 1:
            raise MultipleStatementsError(
                f"Only one SQL statement is allowed per run; got {len(non_empty)}."
            )

    def _record_history(
        self,
        *,
        conn_id: int,
        sql: str,
        duration_ms: int,
        row_count: int | None,
        success: bool,
        error_message: str | None,
    ) -> None:
        """Insert a QueryHistory record, ignoring any persistence errors."""
        try:
            self._repository.add_history(
                QueryHistory(
                    id=0,  # ignored; auto-assigned by repository
                    connection_id=conn_id,
                    sql=sql,
                    executed_at=_now_utc(),
                    duration_ms=duration_ms,
                    row_count=row_count,
                    success=success,
                    error_message=error_message,
                )
            )
        except Exception:
            # History recording must never crash the executor.
            pass
