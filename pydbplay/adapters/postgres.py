"""PostgreSQL adapter — SQLAlchemy Core engine, psycopg 3 sync driver."""

import time
from collections.abc import Iterator
from typing import Any

from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL, Engine

from pydbplay.adapters.base import (
    AdapterError,
    DBAdapter,
    ReadOnlyViolationError,
    UnknownIdentifierError,
)
from pydbplay.core.sql_validator import is_read_only_statement
from pydbplay.schemas.query import QueryResult
from pydbplay.schemas.schema import (
    ColumnInfo,
    ForeignKeyInfo,
    IndexInfo,
    TableInfo,
    TableSchema,
)


def _make_engine(
    *,
    host: str,
    port: int,
    database: str,
    username: str,
    password: str | None,
    ssl_mode: str | None,
) -> Engine:
    """Build a SQLAlchemy Engine for PostgreSQL via psycopg 3 (sync)."""
    connect_args: dict[str, Any] = {}
    if ssl_mode:
        connect_args["sslmode"] = ssl_mode

    url = URL.create(
        drivername="postgresql+psycopg",
        username=username,
        password=password,
        host=host,
        port=port,
        database=database,
    )
    return create_engine(
        url,
        pool_size=5,
        pool_pre_ping=True,
        pool_recycle=300,
        connect_args=connect_args,
    )


class PostgresAdapter(DBAdapter):
    """DBAdapter implementation for PostgreSQL via psycopg 3 sync driver.

    Uses SQLAlchemy Core — NOT the ORM (SPEC §9).  FastAPI routes that call
    this adapter must be ``def`` (Starlette runs them in a thread pool).

    Args:
        host: Hostname or IP of the Postgres server.
        port: TCP port (usually 5432).
        database: Database name.
        username: Login role name.
        password: Plaintext password (NOTE: encryption is a later phase —
                  see TODO below).
        ssl_mode: Optional ``sslmode`` value passed to psycopg (e.g. ``"require"``).
        read_only: When True, ``execute()`` and ``execute_stream()`` reject any
                   non-SELECT/EXPLAIN/SHOW statement before it reaches the DB.
    """

    dialect = "postgres"

    def __init__(
        self,
        *,
        host: str,
        port: int,
        database: str,
        username: str,
        password: str | None = None,
        ssl_mode: str | None = None,
        read_only: bool = False,
    ) -> None:
        self._host = host
        self._port = port
        self._database = database
        self._username = username
        self._ssl_mode = ssl_mode
        self._read_only = read_only
        # TODO(phase-crypto): decrypt password before use
        self._engine: Engine = _make_engine(
            host=host,
            port=port,
            database=database,
            username=username,
            password=password,
            ssl_mode=ssl_mode,
        )

    # ------------------------------------------------------------------
    # Internal helper
    # ------------------------------------------------------------------

    def _check_read_only(self, sql: str) -> None:
        """Raise ReadOnlyViolationError if *sql* is not read-only."""
        if not is_read_only_statement(sql, "postgres", allow_show=True):
            raise ReadOnlyViolationError("Statement is not allowed in read-only mode")

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
        """Return user-visible schema names, excluding pg_catalog internals."""
        sql = text(
            """
            SELECT schema_name
            FROM information_schema.schemata
            WHERE schema_name NOT IN ('pg_catalog', 'information_schema')
              AND schema_name NOT LIKE 'pg\\_%' ESCAPE '\\'
            ORDER BY schema_name
            """
        )
        with self._engine.connect() as conn:
            rows = conn.execute(sql).fetchall()
        return [str(row[0]) for row in rows]

    def list_tables(self, schema: str | None = None) -> list[TableInfo]:
        """Return tables and views in *schema* (default ``"public"``)."""
        effective_schema = schema or "public"
        sql = text(
            """
            SELECT table_name, table_type
            FROM information_schema.tables
            WHERE table_schema = :schema
            ORDER BY table_name
            """
        ).bindparams(schema=effective_schema)

        with self._engine.connect() as conn:
            rows = conn.execute(sql).fetchall()

        result: list[TableInfo] = []
        for row in rows:
            tname = str(row[0])
            ttype = str(row[1])
            # information_schema uses "BASE TABLE" and "VIEW"
            table_type = "VIEW" if ttype == "VIEW" else "BASE TABLE"
            result.append(
                TableInfo(
                    name=tname,
                    schema=effective_schema,
                    table_type=table_type,
                )
            )
        return result

    def describe_table(self, table: str, schema: str | None = None) -> TableSchema:
        """Describe a table using information_schema and pg_indexes."""
        effective_schema = schema or "public"

        with self._engine.connect() as conn:
            # -- Columns --------------------------------------------------
            col_sql = text(
                """
                SELECT column_name,
                       data_type,
                       is_nullable,
                       column_default
                FROM information_schema.columns
                WHERE table_schema = :schema
                  AND table_name   = :table
                ORDER BY ordinal_position
                """
            ).bindparams(schema=effective_schema, table=table)
            col_rows = conn.execute(col_sql).fetchall()

            # -- Primary key columns --------------------------------------
            pk_sql = text(
                """
                SELECT kcu.column_name
                FROM information_schema.table_constraints tc
                JOIN information_schema.key_column_usage kcu
                  ON tc.constraint_name = kcu.constraint_name
                 AND tc.table_schema    = kcu.table_schema
                 AND tc.table_name      = kcu.table_name
                WHERE tc.constraint_type = 'PRIMARY KEY'
                  AND tc.table_schema    = :schema
                  AND tc.table_name      = :table
                ORDER BY kcu.ordinal_position
                """
            ).bindparams(schema=effective_schema, table=table)
            pk_rows = conn.execute(pk_sql).fetchall()
            pk_cols: set[str] = {str(r[0]) for r in pk_rows}

            # -- Indexes --------------------------------------------------
            idx_sql = text(
                """
                SELECT indexname, indexdef
                FROM pg_indexes
                WHERE schemaname = :schema
                  AND tablename  = :table
                ORDER BY indexname
                """
            ).bindparams(schema=effective_schema, table=table)
            idx_rows = conn.execute(idx_sql).fetchall()

            # -- Foreign keys ---------------------------------------------
            fk_sql = text(
                """
                SELECT
                    rc.constraint_name,
                    kcu_fk.column_name       AS fk_column,
                    ccu.table_name           AS ref_table,
                    ccu.column_name          AS ref_column,
                    rc.update_rule           AS on_update,
                    rc.delete_rule           AS on_delete
                FROM information_schema.referential_constraints rc
                JOIN information_schema.key_column_usage kcu_fk
                  ON kcu_fk.constraint_name  = rc.constraint_name
                 AND kcu_fk.constraint_schema = rc.constraint_schema
                JOIN information_schema.constraint_column_usage ccu
                  ON ccu.constraint_name  = rc.unique_constraint_name
                 AND ccu.constraint_schema = rc.constraint_schema
                WHERE kcu_fk.table_schema = :schema
                  AND kcu_fk.table_name   = :table
                ORDER BY rc.constraint_name, kcu_fk.ordinal_position
                """
            ).bindparams(schema=effective_schema, table=table)
            fk_rows = conn.execute(fk_sql).fetchall()

        # -- Build ColumnInfo list ----------------------------------------
        columns: list[ColumnInfo] = []
        for col_row in col_rows:
            col_name = str(col_row[0])
            data_type = str(col_row[1])
            nullable = str(col_row[2]).upper() == "YES"
            default_val = str(col_row[3]) if col_row[3] is not None else None
            columns.append(
                ColumnInfo(
                    name=col_name,
                    data_type=data_type,
                    is_nullable=nullable,
                    is_primary_key=col_name in pk_cols,
                    default_value=default_val,
                )
            )

        # -- Build IndexInfo list -----------------------------------------
        indexes: list[IndexInfo] = []
        for idx_row in idx_rows:
            idx_name = str(idx_row[0])
            idx_def = str(idx_row[1]).upper()
            is_unique = "UNIQUE" in idx_def
            is_primary = idx_name.endswith("_pkey") or "PRIMARY KEY" in idx_def
            # Extract column names from the index definition
            # pg_indexes.indexdef looks like: CREATE [UNIQUE] INDEX name ON table USING btree (col1, col2)
            # TODO(phase-2): expression/partial indexes via pg_index/pg_attribute
            #   The current slice between first '(' and last ')' mis-parses expression
            #   indexes (e.g. ON t (lower(name))) and partial indexes (… WHERE x > 0).
            #   Fix by joining pg_index with pg_attribute to get column names directly.
            idx_cols: list[str] = []
            paren_start = idx_def.find("(")
            paren_end = idx_def.rfind(")")
            if paren_start != -1 and paren_end != -1:
                cols_str = str(idx_row[1])[paren_start + 1 : paren_end]
                idx_cols = [c.strip().strip('"') for c in cols_str.split(",")]
            indexes.append(
                IndexInfo(
                    name=idx_name,
                    columns=idx_cols,
                    is_unique=is_unique,
                    is_primary=is_primary,
                )
            )

        # -- Build ForeignKeyInfo list ------------------------------------
        fk_map: dict[str, dict[str, Any]] = {}
        for fk_row in fk_rows:
            cname = str(fk_row[0])
            if cname not in fk_map:
                fk_map[cname] = {
                    "ref_table": str(fk_row[2]),
                    "columns": [],
                    "ref_columns": [],
                    "on_update": str(fk_row[4]) if fk_row[4] else None,
                    "on_delete": str(fk_row[5]) if fk_row[5] else None,
                }
            fk_map[cname]["columns"].append(str(fk_row[1]))
            fk_map[cname]["ref_columns"].append(str(fk_row[3]))

        foreign_keys: list[ForeignKeyInfo] = [
            ForeignKeyInfo(
                name=cname,
                columns=v["columns"],
                ref_table=v["ref_table"],
                ref_columns=v["ref_columns"],
                on_update=v["on_update"],
                on_delete=v["on_delete"],
            )
            for cname, v in fk_map.items()
        ]

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

        Enforces the read-only guard when ``self._read_only`` is True.

        Args:
            sql: SQL statement to execute.
            params: Optional bind parameters (safe, parameterized).

        Returns:
            QueryResult with columns, rows (list[list]), row_count, duration_ms.

        Raises:
            ReadOnlyViolationError: When read_only=True and sql is a write statement.
            AdapterError: On DB-level errors.
        """
        if self._read_only:
            self._check_read_only(sql)

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
        """Execute *sql* and yield rows in chunks using a server-side cursor.

        Never calls ``fetchall()`` — uses ``fetchmany(chunk_size)`` (SPEC §9).
        Streaming is always read-only by contract; the read-only guard is applied
        regardless of ``self._read_only``.

        Args:
            sql: SELECT statement to stream.
            chunk_size: Number of rows per yielded chunk.

        Yields:
            Lists of row dicts (column_name → value).
        """
        if self._read_only:
            self._check_read_only(sql)
        elif not is_read_only_statement(sql, "postgres", allow_show=True):
            raise AdapterError("execute_stream supports only row-returning read queries")

        with self._engine.connect().execution_options(
            stream_results=True,
            max_row_buffer=chunk_size,
        ) as conn:
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
            raise UnknownIdentifierError(f"Identifier {name!r} is not in the known-safe set")
        return self.quote_identifier(name)

    # ------------------------------------------------------------------
    # Primary key helpers
    # ------------------------------------------------------------------

    def get_pk_columns(self, table: str, schema: str | None = None) -> list[str]:
        """Return PK column names for *table* in order.

        Args:
            table: Unquoted table name.
            schema: Schema name; None means ``"public"``.

        Returns:
            Ordered list of PK column name strings.
        """
        effective_schema = schema or "public"
        sql = text(
            """
            SELECT kcu.column_name
            FROM information_schema.table_constraints tc
            JOIN information_schema.key_column_usage kcu
              ON tc.constraint_name = kcu.constraint_name
             AND tc.table_schema    = kcu.table_schema
             AND tc.table_name      = kcu.table_name
            WHERE tc.constraint_type = 'PRIMARY KEY'
              AND tc.table_schema    = :schema
              AND tc.table_name      = :table
            ORDER BY kcu.ordinal_position
            """
        ).bindparams(schema=effective_schema, table=table)
        with self._engine.connect() as conn:
            rows = conn.execute(sql).fetchall()
        return [str(row[0]) for row in rows]
