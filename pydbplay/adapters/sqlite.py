"""SQLite adapter — SQLAlchemy Core engine, sync, no FastAPI dependency."""

import re
import sqlite3
import time
from collections.abc import Iterator
from typing import Any

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine

from pydbplay.adapters.base import (
    AdapterError,
    DBAdapter,
    ReadOnlyViolationError,
    UnknownIdentifierError,
)
from pydbplay.schemas.query import QueryResult
from pydbplay.schemas.schema import (
    ColumnInfo,
    ForeignKeyInfo,
    IndexInfo,
    TableInfo,
    TableSchema,
)

# PRAGMAs that are purely informational and safe in read-only mode.
# Write-PRAGMAs (those using the `PRAGMA name = value` assignment form) are
# handled by the assignment-detection regex and are always rejected.
_READ_PRAGMA_ALLOWLIST: frozenset[str] = frozenset(
    {
        "table_info",
        "table_xinfo",
        "index_list",
        "index_info",
        "index_xinfo",
        "foreign_key_list",
        "database_list",
        "collation_list",
        "table_list",
    }
)

# Matches: PRAGMA [schema.]name  or  PRAGMA [schema.]name(args)
# Does NOT match assignment form (PRAGMA name = value).
_PRAGMA_READ_RE = re.compile(
    r"^\s*PRAGMA\s+(?:[A-Za-z_][A-Za-z0-9_]*\s*\.\s*)?([A-Za-z_][A-Za-z0-9_]*)"
    r"\s*(?:\([^)]*\))?\s*;?\s*$",
    re.IGNORECASE,
)
# Matches the assignment form: PRAGMA [schema.]name = ...  or  PRAGMA [schema.]name=...
_PRAGMA_WRITE_RE = re.compile(
    r"^\s*PRAGMA\s+(?:[A-Za-z_][A-Za-z0-9_]*\s*\.\s*)?[A-Za-z_][A-Za-z0-9_]*\s*=",
    re.IGNORECASE,
)
# Matches EXPLAIN [QUERY PLAN] prefix
_EXPLAIN_RE = re.compile(
    r"^\s*EXPLAIN\s+(?:QUERY\s+PLAN\s+)?",
    re.IGNORECASE,
)
# Matches a SELECT statement (plain or CTE: WITH ... SELECT)
_SELECT_RE = re.compile(
    r"^\s*(?:WITH\b.*?\bSELECT\b|SELECT\b)",
    re.IGNORECASE | re.DOTALL,
)


def _is_read_only_statement(sql: str) -> bool:
    """Return True only for statements that are safe to execute in read-only mode.

    A statement is a read iff:
    - It is a SELECT (including leading CTE ``WITH … SELECT``), OR
    - It is ``EXPLAIN`` / ``EXPLAIN QUERY PLAN`` wrapping a read (strip the
      prefix and re-check the inner statement), OR
    - It is a **read PRAGMA**: matches ``PRAGMA <name>`` or
      ``PRAGMA <name>(args)`` with NO ``=`` assignment, AND ``<name>`` is in
      ``_READ_PRAGMA_ALLOWLIST``.

    Everything else is considered a write and returns False.
    """
    stripped = sql.strip()

    # Handle EXPLAIN / EXPLAIN QUERY PLAN — strip prefix and re-check inner stmt
    explain_match = _EXPLAIN_RE.match(stripped)
    if explain_match:
        inner = stripped[explain_match.end():]
        return _is_read_only_statement(inner)

    # SELECT (plain or CTE)
    if _SELECT_RE.match(stripped):
        return True

    # PRAGMA — reject assignment forms immediately
    if _PRAGMA_WRITE_RE.match(stripped):
        return False

    pragma_match = _PRAGMA_READ_RE.match(stripped)
    if pragma_match:
        pragma_name = pragma_match.group(1).lower()
        return pragma_name in _READ_PRAGMA_ALLOWLIST

    return False


def _make_engine(database: str) -> Engine:
    """Build a SQLAlchemy Engine for a SQLite file path."""
    engine = create_engine(
        f"sqlite:///{database}",
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine, "connect")
    def _enable_fk(dbapi_conn: object, _record: object) -> None:
        assert isinstance(dbapi_conn, sqlite3.Connection)
        dbapi_conn.execute("PRAGMA foreign_keys=ON")

    return engine


class SQLiteAdapter(DBAdapter):
    """DBAdapter implementation for SQLite via SQLAlchemy Core (sync).

    Args:
        database: Filesystem path to the SQLite file (maps to
                  ``ConnectionProfile.database``).
        read_only: When True, ``execute()`` rejects any non-SELECT/EXPLAIN/PRAGMA
                   statement before it reaches the DB (SPEC §9).
    """

    def __init__(self, database: str, *, read_only: bool = False) -> None:
        self._database = database
        self._read_only = read_only
        self._engine: Engine = _make_engine(database)

    # ------------------------------------------------------------------
    # Connection health
    # ------------------------------------------------------------------

    def test_connection(self) -> bool:
        """Open a connection, execute SELECT 1, return True on success."""
        try:
            with self._engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            return True
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Schema introspection
    # ------------------------------------------------------------------

    def list_schemas(self) -> list[str]:
        """Return database names from PRAGMA database_list (normally ["main"])."""
        with self._engine.connect() as conn:
            rows = conn.execute(text("PRAGMA database_list")).fetchall()
        # Each row: (seq, name, file)
        return sorted(str(row[1]) for row in rows)

    def list_tables(self, schema: str | None = None) -> list[TableInfo]:
        """Return tables and views from sqlite_master, excluding sqlite_ internals."""
        effective_schema = schema or "main"
        # sqlite_master is schema-qualified as <schema>.sqlite_master
        quoted_master = f"{self.quote_identifier(effective_schema)}.sqlite_master"
        sql = (
            f"SELECT name, type FROM {quoted_master} "
            "WHERE type IN ('table', 'view') AND name NOT LIKE 'sqlite_%' "
            "ORDER BY name"
        )
        with self._engine.connect() as conn:
            rows = conn.execute(text(sql)).fetchall()

        result: list[TableInfo] = []
        for row in rows:
            tname = str(row[0])
            ttype = str(row[1])
            table_type = "VIEW" if ttype == "view" else "BASE TABLE"
            result.append(
                TableInfo(
                    name=tname,
                    schema=effective_schema,
                    table_type=table_type,
                )
            )
        return result

    def describe_table(self, table: str, schema: str | None = None) -> TableSchema:
        """Describe a table using PRAGMA table_info, index_list/index_info, and foreign_key_list."""
        effective_schema = schema or "main"
        quoted_table = self.quote_identifier(table)

        with self._engine.connect() as conn:
            # -- Columns --------------------------------------------------
            col_rows = conn.execute(
                text(f"PRAGMA {self.quote_identifier(effective_schema)}.table_info({quoted_table})")
            ).fetchall()

            # -- Indexes --------------------------------------------------
            idx_rows = conn.execute(
                text(
                    f"PRAGMA {self.quote_identifier(effective_schema)}.index_list({quoted_table})"
                )
            ).fetchall()

            indexes: list[IndexInfo] = []
            for idx_row in idx_rows:
                idx_name = str(idx_row[1])
                is_unique = bool(idx_row[2])
                # origin: 'c'=CREATE INDEX, 'u'=UNIQUE constraint, 'pk'=PRIMARY KEY
                origin = str(idx_row[3]) if len(idx_row) > 3 else "c"
                is_primary = origin == "pk"

                info_rows = conn.execute(
                    text(
                        f"PRAGMA {self.quote_identifier(effective_schema)}"
                        f".index_info({self.quote_identifier(idx_name)})"
                    )
                ).fetchall()
                idx_cols = [str(r[2]) for r in info_rows]  # r[2] = column name

                indexes.append(
                    IndexInfo(
                        name=idx_name,
                        columns=idx_cols,
                        is_unique=is_unique,
                        is_primary=is_primary,
                    )
                )

            # -- Foreign keys ---------------------------------------------
            fk_rows = conn.execute(
                text(
                    f"PRAGMA {self.quote_identifier(effective_schema)}"
                    f".foreign_key_list({quoted_table})"
                )
            ).fetchall()

        # Group FK rows by FK id (multiple rows = composite FK)
        fk_map: dict[int, dict[str, Any]] = {}
        for fk_row in fk_rows:
            fk_id = int(fk_row[0])
            if fk_id not in fk_map:
                fk_map[fk_id] = {
                    "ref_table": str(fk_row[2]),
                    "columns": [],
                    "ref_columns": [],
                    "on_update": str(fk_row[5]) if fk_row[5] else None,
                    "on_delete": str(fk_row[6]) if fk_row[6] else None,
                }
            fk_map[fk_id]["columns"].append(str(fk_row[3]))
            fk_map[fk_id]["ref_columns"].append(str(fk_row[4]))

        foreign_keys: list[ForeignKeyInfo] = [
            ForeignKeyInfo(
                columns=v["columns"],
                ref_table=v["ref_table"],
                ref_columns=v["ref_columns"],
                on_update=v["on_update"],
                on_delete=v["on_delete"],
            )
            for v in fk_map.values()
        ]

        # Build ColumnInfo list
        # PRAGMA table_info columns: cid, name, type, notnull, dflt_value, pk
        columns: list[ColumnInfo] = []
        for col_row in col_rows:
            columns.append(
                ColumnInfo(
                    name=str(col_row[1]),
                    data_type=str(col_row[2]),
                    is_nullable=not bool(col_row[3]),
                    is_primary_key=bool(col_row[5]),
                    default_value=str(col_row[4]) if col_row[4] is not None else None,
                )
            )

        return TableSchema(
            name=table,
            schema=effective_schema,
            columns=columns,
            indexes=indexes,
            foreign_keys=foreign_keys,
        )

    # ------------------------------------------------------------------
    # Query execution
    # ------------------------------------------------------------------

    def execute(self, sql: str, params: dict | None = None) -> QueryResult:
        """Execute *sql* with optional bind *params* and return a QueryResult.

        Enforces the read-only guard when ``self._read_only`` is True by
        parsing the SQL with sqlglot and rejecting any statement whose type
        is not in ``_READ_ONLY_ALLOWED``.

        Args:
            sql: SQL statement to execute.
            params: Optional bind parameters (safe, parameterized).

        Returns:
            QueryResult with columns, rows (list[list]), row_count, duration_ms.

        Raises:
            ReadOnlyViolationError: When read_only=True and sql is a write statement.
            AdapterError: On DB-level errors.
        """
        if self._read_only and not _is_read_only_statement(sql):
            raise ReadOnlyViolationError(
                "Statement is not allowed in read-only mode"
            )

        try:
            start = time.monotonic()
            with self._engine.begin() as conn:
                result = conn.execute(text(sql), params or {})
                if result.returns_rows:
                    col_names = list(result.keys())
                    raw_rows = result.fetchall()
                    rows: list[list[Any]] = [list(row) for row in raw_rows]
                    row_count = len(rows)
                else:
                    col_names = []
                    rows = []
                    row_count = result.rowcount if result.rowcount >= 0 else 0
            duration_ms = int((time.monotonic() - start) * 1000)
        except (AdapterError, ReadOnlyViolationError):
            raise
        except Exception as exc:
            raise AdapterError(str(exc)) from exc

        return QueryResult(
            columns=col_names,
            rows=rows,
            row_count=row_count,
            duration_ms=duration_ms,
        )

    def execute_stream(self, sql: str, chunk_size: int = 1000) -> Iterator[list[dict]]:
        """Execute *sql* and yield rows in chunks, keeping the connection open.

        Uses ``fetchmany(chunk_size)`` — never ``fetchall()`` (SPEC §9).

        Args:
            sql: SELECT statement to stream.
            chunk_size: Number of rows per yielded chunk.

        Yields:
            Lists of row dicts (column_name → value).
        """
        if not _is_read_only_statement(sql):
            if self._read_only:
                raise ReadOnlyViolationError(
                    "Statement is not allowed in read-only mode"
                )
            raise AdapterError(
                "execute_stream supports only row-returning read queries"
            )

        with self._engine.connect() as conn:
            result = conn.execute(text(sql))
            col_names = list(result.keys())
            while True:
                batch = result.fetchmany(chunk_size)
                if not batch:
                    break
                yield [dict(zip(col_names, row, strict=False)) for row in batch]

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def dispose(self) -> None:
        """Dispose the SQLAlchemy engine, closing all pooled connections."""
        self._engine.dispose()

    # ------------------------------------------------------------------
    # Identifier safety
    # ------------------------------------------------------------------

    def quote_identifier(self, name: str) -> str:
        """Return *name* wrapped in double-quotes, with embedded quotes escaped."""
        escaped = name.replace('"', '""')
        return f'"{escaped}"'

    def validate_identifier(self, name: str, *, known: set[str]) -> str:
        """Whitelist *name* against *known* and return the quoted identifier.

        Args:
            name: Identifier to validate.
            known: Set of known-safe identifiers from the live schema.

        Returns:
            Quoted identifier string.

        Raises:
            UnknownIdentifierError: When *name* is not in *known*.
        """
        if name not in known:
            raise UnknownIdentifierError(
                f"Identifier {name!r} is not in the known-safe set"
            )
        return self.quote_identifier(name)

    # ------------------------------------------------------------------
    # Primary key helpers
    # ------------------------------------------------------------------

    def get_pk_columns(self, table: str, schema: str | None = None) -> list[str]:
        """Return PK column names for *table*, ordered by their pk index.

        Uses ``PRAGMA table_info`` — columns with ``pk > 0`` form the PK.

        Args:
            table: Unquoted table name.
            schema: Schema name; None means "main".

        Returns:
            Ordered list of PK column name strings.
        """
        effective_schema = schema or "main"
        quoted_table = self.quote_identifier(table)
        with self._engine.connect() as conn:
            rows = conn.execute(
                text(
                    f"PRAGMA {self.quote_identifier(effective_schema)}.table_info({quoted_table})"
                )
            ).fetchall()

        # PRAGMA table_info: cid, name, type, notnull, dflt_value, pk
        pk_cols = [(int(row[5]), str(row[1])) for row in rows if int(row[5]) > 0]
        pk_cols.sort(key=lambda t: t[0])
        return [name for _, name in pk_cols]
