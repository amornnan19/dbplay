"""MySQL / MariaDB adapter — SQLAlchemy Core engine, PyMySQL sync driver."""

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
    """Build a SQLAlchemy Engine for MySQL via PyMySQL (sync)."""
    connect_args: dict[str, Any] = {}
    if ssl_mode:
        # TODO(phase-ssl): map ssl_mode to PyMySQL ssl config (SPEC §11, deferred).
        # Do NOT pass a ssl connect_arg yet — {"ca": None} is a broken PyMySQL config
        # that silently disables SSL rather than enabling it.
        pass

    url = URL.create(
        drivername="mysql+pymysql",
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


class MySQLAdapter(DBAdapter):
    """DBAdapter implementation for MySQL / MariaDB via PyMySQL sync driver.

    Uses SQLAlchemy Core — NOT the ORM (SPEC §9).  FastAPI routes that call
    this adapter must be ``def`` (Starlette runs them in a thread pool).

    In MySQL, "schema" == "database".  All schema-scoped queries use
    ``information_schema`` with ``table_schema = :schema`` bound parameters.
    The default schema is the connected database (``self._database``).

    Args:
        host: Hostname or IP of the MySQL server.
        port: TCP port (usually 3306).
        database: Database name (also the default schema).
        username: Login user name.
        password: Plaintext password (NOTE: encryption is a later phase —
                  see TODO below).
        ssl_mode: Optional SSL mode string; passed through to PyMySQL ssl dict.
        read_only: When True, ``execute()`` and ``execute_stream()`` reject any
                   non-SELECT/EXPLAIN/SHOW statement before it reaches the DB.
    """

    dialect = "mysql"

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
        if not is_read_only_statement(sql, "mysql", allow_show=True):
            raise ReadOnlyViolationError("Statement is not allowed in read-only mode")

    def _effective_schema(self, schema: str | None) -> str:
        """Return *schema* if given, else the connected database name."""
        return schema if schema is not None else self._database

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
        """Return user-visible database/schema names, excluding MySQL internals."""
        sql = text(
            """
            SELECT schema_name
            FROM information_schema.schemata
            WHERE schema_name NOT IN (
                'mysql', 'information_schema', 'performance_schema', 'sys'
            )
            ORDER BY schema_name
            """
        )
        try:
            with self._engine.connect() as conn:
                rows = conn.execute(sql).fetchall()
        except (AdapterError, ReadOnlyViolationError):
            raise
        except Exception as exc:
            raise AdapterError(str(exc)) from exc
        return [str(row[0]) for row in rows]

    def list_tables(self, schema: str | None = None) -> list[TableInfo]:
        """Return tables and views in *schema* (default: connected database)."""
        effective_schema = self._effective_schema(schema)
        sql = text(
            """
            SELECT table_name, table_type
            FROM information_schema.tables
            WHERE table_schema = :schema
            ORDER BY table_name
            """
        ).bindparams(schema=effective_schema)

        try:
            with self._engine.connect() as conn:
                rows = conn.execute(sql).fetchall()
        except (AdapterError, ReadOnlyViolationError):
            raise
        except Exception as exc:
            raise AdapterError(str(exc)) from exc

        result: list[TableInfo] = []
        for row in rows:
            tname = str(row[0])
            ttype = str(row[1])
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
        """Describe a table using information_schema columns, statistics, and key_column_usage."""
        effective_schema = self._effective_schema(schema)

        try:
            with self._engine.connect() as conn:
                # -- Columns ----------------------------------------------
                col_sql = text(
                    """
                    SELECT column_name,
                           column_type,
                           is_nullable,
                           column_default
                    FROM information_schema.columns
                    WHERE table_schema = :schema
                      AND table_name   = :table
                    ORDER BY ordinal_position
                    """
                ).bindparams(schema=effective_schema, table=table)
                col_rows = conn.execute(col_sql).fetchall()

                # -- Primary key columns ----------------------------------
                pk_sql = text(
                    """
                    SELECT column_name
                    FROM information_schema.key_column_usage
                    WHERE table_schema      = :schema
                      AND table_name        = :table
                      AND constraint_name   = 'PRIMARY'
                    ORDER BY ordinal_position
                    """
                ).bindparams(schema=effective_schema, table=table)
                pk_rows = conn.execute(pk_sql).fetchall()
                pk_cols: set[str] = {str(r[0]) for r in pk_rows}

                # -- Indexes ----------------------------------------------
                # information_schema.statistics: one row per index column
                idx_sql = text(
                    """
                    SELECT index_name,
                           column_name,
                           non_unique
                    FROM information_schema.statistics
                    WHERE table_schema = :schema
                      AND table_name   = :table
                    ORDER BY index_name, seq_in_index
                    """
                ).bindparams(schema=effective_schema, table=table)
                idx_rows = conn.execute(idx_sql).fetchall()

                # -- Foreign keys -----------------------------------------
                fk_sql = text(
                    """
                    SELECT
                        kcu.constraint_name,
                        kcu.column_name           AS fk_column,
                        kcu.referenced_table_name  AS ref_table,
                        kcu.referenced_column_name AS ref_column,
                        rc.update_rule             AS on_update,
                        rc.delete_rule             AS on_delete
                    FROM information_schema.key_column_usage kcu
                    JOIN information_schema.referential_constraints rc
                      ON rc.constraint_name   = kcu.constraint_name
                     AND rc.constraint_schema = kcu.table_schema
                    WHERE kcu.table_schema = :schema
                      AND kcu.table_name   = :table
                      AND kcu.referenced_table_name IS NOT NULL
                    ORDER BY kcu.constraint_name, kcu.ordinal_position
                    """
                ).bindparams(schema=effective_schema, table=table)
                fk_rows = conn.execute(fk_sql).fetchall()
        except (AdapterError, ReadOnlyViolationError):
            raise
        except Exception as exc:
            raise AdapterError(str(exc)) from exc

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
        idx_map: dict[str, dict[str, Any]] = {}
        for idx_row in idx_rows:
            iname = str(idx_row[0])
            icol = str(idx_row[1])
            non_unique = bool(idx_row[2])
            if iname not in idx_map:
                idx_map[iname] = {
                    "columns": [],
                    "is_unique": not non_unique,
                    "is_primary": iname == "PRIMARY",
                }
            idx_map[iname]["columns"].append(icol)

        indexes: list[IndexInfo] = [
            IndexInfo(
                name=iname,
                columns=v["columns"],
                is_unique=v["is_unique"],
                is_primary=v["is_primary"],
            )
            for iname, v in idx_map.items()
        ]

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
        """Execute *sql* and yield rows in chunks using PyMySQL's SSCursor.

        Never calls ``fetchall()`` — uses ``fetchmany(chunk_size)`` (SPEC §9).
        SQLAlchemy honors ``stream_results=True`` on mysql+pymysql to use
        PyMySQL's unbuffered SSCursor.
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
        elif not is_read_only_statement(sql, "mysql", allow_show=True):
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
        """Return *name* wrapped in backticks, with embedded backticks doubled.

        MySQL uses backtick quoting.  An embedded backtick is escaped by
        doubling it: `` ` `` → ` `` `.
        """
        escaped = name.replace("`", "``")
        return f"`{escaped}`"

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
            schema: Schema name; None means the connected database.

        Returns:
            Ordered list of PK column name strings.
        """
        effective_schema = self._effective_schema(schema)
        sql = text(
            """
            SELECT column_name
            FROM information_schema.key_column_usage
            WHERE table_schema    = :schema
              AND table_name      = :table
              AND constraint_name = 'PRIMARY'
            ORDER BY ordinal_position
            """
        ).bindparams(schema=effective_schema, table=table)
        try:
            with self._engine.connect() as conn:
                rows = conn.execute(sql).fetchall()
        except (AdapterError, ReadOnlyViolationError):
            raise
        except Exception as exc:
            raise AdapterError(str(exc)) from exc
        return [str(row[0]) for row in rows]
