"""Abstract DBAdapter interface — FULLY IMPLEMENTED (not a stub).

All adapter methods are synchronous. FastAPI routes that call adapters must be
plain ``def`` so Starlette runs them in a threadpool. Do NOT use async drivers.
"""

from abc import ABC, abstractmethod
from collections.abc import Iterator

from pydbplay.schemas.query import QueryResult
from pydbplay.schemas.schema import TableInfo, TableSchema

# ---------------------------------------------------------------------------
# Adapter exception hierarchy (FastAPI-free — safe to import anywhere)
# ---------------------------------------------------------------------------


class AdapterError(Exception):
    """Base class for all adapter-level errors."""


class ReadOnlyViolationError(AdapterError):
    """Raised when a write statement is submitted to a read-only adapter."""


class UnknownIdentifierError(AdapterError):
    """Raised when an identifier is not in the known-safe whitelist."""


# ---------------------------------------------------------------------------
# Abstract base
# ---------------------------------------------------------------------------


class DBAdapter(ABC):
    """Interface shared by every DB engine adapter. Sync throughout.

    Route handlers must be ``def`` (not ``async def``) so Starlette
    dispatches them to the threadpool automatically.

    Read-only enforcement (SPEC §9):
        When ``ConnectionProfile.read_only`` is True, ``execute()`` must parse
        the statement with sqlglot and reject anything that is not
        SELECT / EXPLAIN before it reaches the DB.

    Identifier safety (SPEC §9):
        ``validate_identifier`` whitelists *name* against *known* before
        quoting. Never skip the whitelist check in the row editor / browse.
    """

    @abstractmethod
    def test_connection(self) -> bool:
        """Verify the connection is live.

        Returns:
            True if the connection succeeds, False otherwise.
        """

    @abstractmethod
    def list_schemas(self) -> list[str]:
        """Return all schema names visible to the connected user.

        Returns:
            Sorted list of schema name strings.
        """

    @abstractmethod
    def list_tables(self, schema: str | None = None) -> list[TableInfo]:
        """Return tables in *schema* (or the DB default if None).

        Args:
            schema: Schema name filter; None means DB-engine default.

        Returns:
            List of TableInfo objects.
        """

    @abstractmethod
    def describe_table(self, table: str, schema: str | None = None) -> TableSchema:
        """Return column metadata, indexes, and FKs for *table*.

        Args:
            table: Unquoted table name.
            schema: Schema name; None means DB-engine default.

        Returns:
            TableSchema with full column and index details.
        """

    @abstractmethod
    def execute(self, sql: str, params: dict | None = None) -> QueryResult:
        """Execute *sql* and return all results.

        Implementation MUST:
        - Enforce read-only guard when ``ConnectionProfile.read_only`` is True
          (reject non-SELECT/EXPLAIN via sqlglot before hitting the DB).
        - Use parameterized queries for all *params* values.
        - NOT call fetchall() on large datasets (use execute_stream instead).

        Args:
            sql: SQL statement to execute.
            params: Optional bind parameters (safe, parameterized).

        Returns:
            QueryResult with columns, rows, duration_ms, row_count.

        Raises:
            ReadOnlyViolation: When profile is read-only and sql is not SELECT/EXPLAIN.
            AdapterError: On DB-level errors.
        """

    @abstractmethod
    def execute_stream(self, sql: str, chunk_size: int = 1000) -> Iterator[list[dict]]:
        """Execute *sql* and yield rows in chunks via a server-side cursor.

        Per SPEC §9: never call fetchall() on large tables.
        Use this for export and browse operations.

        Args:
            sql: SELECT statement to stream.
            chunk_size: Number of rows to fetch per batch.

        Yields:
            Lists of row dicts (column_name → value), chunk_size rows at a time.
        """

    @abstractmethod
    def quote_identifier(self, name: str) -> str:
        """Return *name* safely quoted for the engine's identifier syntax.

        PostgreSQL/SQLite: ``"name"``
        MySQL: `` `name` ``

        Note: Quoting alone is insufficient for safety — always call
        ``validate_identifier`` first to whitelist against the live schema.

        Args:
            name: Unquoted identifier (table, column, or schema name).

        Returns:
            Quoted identifier string.
        """

    @abstractmethod
    def validate_identifier(self, name: str, *, known: set[str]) -> str:
        """Whitelist *name* against *known* then return ``quote_identifier(name)``.

        Per SPEC §9 Identifier safety: table/column/schema names cannot be
        parameterized — they must be whitelisted against the live schema
        (from ``describe_table``) before being embedded in SQL.

        Args:
            name: Identifier to validate.
            known: Set of known-safe identifier names from the live schema.

        Returns:
            Quoted identifier string.

        Raises:
            ValueError: When *name* is not in *known*.
        """

    @abstractmethod
    def get_pk_columns(self, table: str, schema: str | None = None) -> list[str]:
        """Return the primary key column names for *table*.

        Args:
            table: Unquoted table name.
            schema: Schema name; None means DB-engine default.

        Returns:
            Ordered list of PK column name strings.
        """
