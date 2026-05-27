"""Row-level CRUD operations with identifier safety and transaction wrapping.

Per SPEC §9 — identifier safety:
1. Whitelist every table/column/schema name against the LIVE schema before quoting.
2. Only then interpolate the QUOTED identifier into the SQL string.
3. All VALUES go through parameterized bind params — never string-interpolate a value.

All mutating statements are executed via ``adapter.execute()`` which internally uses
``engine.begin()`` (auto-commit on success, auto-rollback on exception).  The
adapter's built-in read-only guard rejects any non-SELECT/EXPLAIN statement on a
read-only connection, so ``ReadOnlyViolationError`` propagates naturally.
"""

from pydbplay.adapters.base import (
    DBAdapter,
    ReadOnlyViolationError,  # noqa: F401 — re-exported so callers can catch it
    UnknownIdentifierError,  # noqa: F401 — re-exported so callers can catch it
)
from pydbplay.core.connection_manager import ConnectionManager
from pydbplay.schemas.rows import BrowseResult

# ---------------------------------------------------------------------------
# Module-level error type
# ---------------------------------------------------------------------------


class RowEditError(Exception):
    """Raised by RowEditor for logical errors (missing PK, empty changes, etc.).

    Distinct from ``UnknownIdentifierError`` (identifier not in schema) and
    ``ReadOnlyViolationError`` (write on a read-only connection) — both of
    those propagate directly from the adapter layer.
    """


# ---------------------------------------------------------------------------
# RowEditor
# ---------------------------------------------------------------------------


class RowEditor:
    """Provides INSERT / UPDATE / DELETE / browse operations on a single table.

    Every mutating operation is executed through ``adapter.execute()`` which
    wraps the statement in ``engine.begin()``; on exception the transaction
    is automatically rolled back (pairs with the optimistic-UI rollback in the
    frontend, SPEC §9).

    Identifier safety (SPEC §9):
    1. Whitelist table/column/schema against ``describe_table`` / ``list_tables`` results.
    2. Call ``adapter.validate_identifier(name, known=<live-set>)`` which raises
       ``UnknownIdentifierError`` if the name is not in the whitelist and returns
       the correctly quoted form otherwise.
    3. Values always use parameterized queries — never string-interpolate a value.

    Args:
        connection_manager: Provides live DBAdapter instances keyed by profile id.
    """

    def __init__(self, connection_manager: ConnectionManager) -> None:
        self._connection_manager = connection_manager

    # ------------------------------------------------------------------
    # Browse
    # ------------------------------------------------------------------

    def browse(
        self,
        conn_id: int,
        table: str,
        *,
        schema: str | None = None,
        page: int = 1,
        page_size: int = 50,
        filters: list[tuple[str, object]] | None = None,
        sort: tuple[str, str] | None = None,
    ) -> BrowseResult:
        """Return a paginated slice of rows from *table*.

        Uses the ``page_size + 1`` trick to detect the next page without a
        ``COUNT(*)`` full scan — if the query returns more than *page_size*
        rows then ``has_next=True`` and the extra row is dropped (SPEC §9).

        Args:
            conn_id: Primary key of the ConnectionProfile.
            table: Unquoted table name.
            schema: Schema name; None means the engine default.
            page: 1-based page number.
            page_size: Maximum rows per page (default 50).
            filters: Optional list of ``(column, value)`` equality conditions
                     joined with AND.  Each column is whitelisted; each value
                     is bound as a parameter.
            sort: Optional ``(column, direction)`` tuple where *direction* must
                  be ``"ASC"`` or ``"DESC"`` (case-sensitive).  The column is
                  whitelisted; direction is validated against the fixed set.

        Returns:
            BrowseResult with columns, rows (≤ page_size), pagination flags, and
            pk_columns.

        Raises:
            UnknownIdentifierError: If *table* or any filter/sort column is not
                in the live schema.
            RowEditError: If *sort* direction is not "ASC" or "DESC".
            KeyError: If *conn_id* does not exist.
        """
        adapter: DBAdapter = self._connection_manager.get_adapter(conn_id)

        # ── Whitelist table ──────────────────────────────────────────────
        known_tables: set[str] = {t.name for t in adapter.list_tables(schema)}
        quoted_table: str = adapter.validate_identifier(table, known=known_tables)

        # ── Fetch column + PK metadata ───────────────────────────────────
        table_schema = adapter.describe_table(table, schema)
        known_columns: set[str] = {c.name for c in table_schema.columns}
        pk_columns: list[str] = adapter.get_pk_columns(table, schema)

        params: dict[str, object] = {}

        # ── WHERE clause ─────────────────────────────────────────────────
        where_parts: list[str] = []
        if filters:
            for idx, (col, val) in enumerate(filters):
                quoted_col = adapter.validate_identifier(col, known=known_columns)
                param_key = f"f{idx}"
                where_parts.append(f"{quoted_col} = :{param_key}")
                params[param_key] = val

        # ── ORDER BY clause ──────────────────────────────────────────────
        order_clause = ""
        if sort is not None:
            sort_col, sort_dir = sort
            sort_dir_upper = sort_dir.upper()
            if sort_dir_upper not in {"ASC", "DESC"}:
                raise RowEditError(f"Invalid sort direction {sort_dir!r}; must be 'ASC' or 'DESC'.")
            quoted_sort_col = adapter.validate_identifier(sort_col, known=known_columns)
            order_clause = f" ORDER BY {quoted_sort_col} {sort_dir_upper}"

        # ── LIMIT / OFFSET (page_size+1 trick) ───────────────────────────
        if page < 1:
            page = 1
        if page_size < 1:
            raise RowEditError("page_size must be >= 1")
        offset = (page - 1) * page_size
        fetch_count = page_size + 1
        params["lim"] = fetch_count
        params["off"] = offset

        # ── Build SQL ─────────────────────────────────────────────────────
        where_clause = f" WHERE {' AND '.join(where_parts)}" if where_parts else ""
        sql = f"SELECT * FROM {quoted_table}{where_clause}{order_clause} LIMIT :lim OFFSET :off"

        result = adapter.execute(sql, params)

        # ── Apply page_size+1 trick ───────────────────────────────────────
        has_next = len(result.rows) > page_size
        rows = result.rows[:page_size]

        return BrowseResult(
            table=table,
            schema_name=schema,
            columns=result.columns,
            rows=rows,
            page=page,
            page_size=page_size,
            has_next=has_next,
            pk_columns=pk_columns,
        )

    # ------------------------------------------------------------------
    # Update
    # ------------------------------------------------------------------

    def update_row(
        self,
        conn_id: int,
        table: str,
        pk_values: dict[str, object],
        changes: dict[str, object],
        *,
        schema: str | None = None,
    ) -> dict[str, object]:
        """Update a single row identified by *pk_values* and return the updated row.

        Args:
            conn_id: Primary key of the ConnectionProfile.
            table: Unquoted table name.
            pk_values: Mapping of PK column name → value identifying the row.
                       Must exactly cover the table's primary key (no partial PK).
            changes: Mapping of column name → new value.  Must be non-empty.
            schema: Schema name; None means the engine default.

        Returns:
            Dict mapping column names to values of the updated row (re-SELECT by PK).
            Returns an empty dict if 0 rows matched the PK (row not found).

        Raises:
            RowEditError: If *changes* is empty, *pk_values* is empty, the table
                has no primary key, or *pk_values* keys don't exactly match the
                table's PK columns.
            UnknownIdentifierError: If *table* or any column in *changes* /
                *pk_values* is not in the live schema.
            ReadOnlyViolationError: If the connection is read-only.
            KeyError: If *conn_id* does not exist.
        """
        if not changes:
            raise RowEditError("'changes' must not be empty.")
        if not pk_values:
            raise RowEditError("'pk_values' must not be empty.")

        adapter: DBAdapter = self._connection_manager.get_adapter(conn_id)

        # ── Whitelist table ──────────────────────────────────────────────
        known_tables: set[str] = {t.name for t in adapter.list_tables(schema)}
        quoted_table: str = adapter.validate_identifier(table, known=known_tables)

        # ── Fetch column + PK metadata ───────────────────────────────────
        table_schema = adapter.describe_table(table, schema)
        known_columns: set[str] = {c.name for c in table_schema.columns}
        pk_columns: list[str] = adapter.get_pk_columns(table, schema)

        if not pk_columns:
            raise RowEditError(f"Table {table!r} has no primary key; UPDATE is not safe.")

        # pk_values must exactly cover the PK (no partial, no extra)
        if set(pk_values.keys()) != set(pk_columns):
            raise RowEditError(
                f"pk_values keys {set(pk_values.keys())!r} must exactly match "
                f"the table's PK columns {set(pk_columns)!r}."
            )

        # Reject changes that touch PK columns — simpler and safer than re-keying
        pk_in_changes = set(changes) & set(pk_columns)
        if pk_in_changes:
            raise RowEditError(f"cannot change primary key column(s): {sorted(pk_in_changes)!r}")

        params: dict[str, object] = {}

        # ── SET clause ────────────────────────────────────────────────────
        set_parts: list[str] = []
        for idx, (col, val) in enumerate(changes.items()):
            quoted_col = adapter.validate_identifier(col, known=known_columns)
            param_key = f"s{idx}"
            set_parts.append(f"{quoted_col} = :{param_key}")
            params[param_key] = val

        # ── WHERE clause (full PK) ────────────────────────────────────────
        where_parts: list[str] = []
        for idx, (col, val) in enumerate(pk_values.items()):
            quoted_col = adapter.validate_identifier(col, known=known_columns)
            param_key = f"p{idx}"
            where_parts.append(f"{quoted_col} = :{param_key}")
            params[param_key] = val

        sql = f"UPDATE {quoted_table} SET {', '.join(set_parts)} WHERE {' AND '.join(where_parts)}"

        adapter.execute(sql, params)

        # Always re-SELECT by PK — do NOT rely on row_count==0 as "not found"
        # because MySQL (by default) reports CHANGED rows, not MATCHED rows, so
        # a no-op update (same values) returns row_count=0 even when the row exists.
        return self._select_row_by_pk(adapter, table, quoted_table, pk_values, known_columns)

    # ------------------------------------------------------------------
    # Insert
    # ------------------------------------------------------------------

    def insert_row(
        self,
        conn_id: int,
        table: str,
        values: dict[str, object],
        *,
        schema: str | None = None,
    ) -> dict[str, object]:
        """Insert a new row into *table*.

        Args:
            conn_id: Primary key of the ConnectionProfile.
            table: Unquoted table name.
            values: Mapping of column name → value for the new row.  Must be
                    non-empty.  Columns not present will use DB defaults.
            schema: Schema name; None means the engine default.

        Returns:
            Dict with ``{"inserted": True, "row_count": 1, "values": values}``.
            Getting the inserted PK back is engine-specific (lastrowid vs
            RETURNING), so the caller should refresh the browse grid after insert.
            # TODO(phase-2b): RETURNING/lastrowid — return the full new row once
            # RETURNING support is standardised across all three adapters.

        Raises:
            RowEditError: If *values* is empty.
            UnknownIdentifierError: If *table* or any column key is not in the
                live schema.
            ReadOnlyViolationError: If the connection is read-only.
            KeyError: If *conn_id* does not exist.
        """
        if not values:
            raise RowEditError("'values' must not be empty.")

        adapter: DBAdapter = self._connection_manager.get_adapter(conn_id)

        # ── Whitelist table ──────────────────────────────────────────────
        known_tables: set[str] = {t.name for t in adapter.list_tables(schema)}
        quoted_table: str = adapter.validate_identifier(table, known=known_tables)

        # ── Fetch column metadata ────────────────────────────────────────
        table_schema = adapter.describe_table(table, schema)
        known_columns: set[str] = {c.name for c in table_schema.columns}

        params: dict[str, object] = {}
        quoted_cols: list[str] = []
        param_placeholders: list[str] = []

        for idx, (col, val) in enumerate(values.items()):
            quoted_col = adapter.validate_identifier(col, known=known_columns)
            param_key = f"v{idx}"
            quoted_cols.append(quoted_col)
            param_placeholders.append(f":{param_key}")
            params[param_key] = val

        cols_clause = ", ".join(quoted_cols)
        vals_clause = ", ".join(param_placeholders)
        sql = f"INSERT INTO {quoted_table} ({cols_clause}) VALUES ({vals_clause})"

        result = adapter.execute(sql, params)

        # TODO(phase-2b): RETURNING/lastrowid — return the full inserted row
        # once RETURNING is standardised across SQLite 3.35+, Postgres, and MySQL.
        return {"inserted": True, "row_count": result.row_count, "values": values}

    # ------------------------------------------------------------------
    # Delete
    # ------------------------------------------------------------------

    def delete_row(
        self,
        conn_id: int,
        table: str,
        pk_values: dict[str, object],
        *,
        schema: str | None = None,
    ) -> bool:
        """Delete the single row identified by *pk_values*.

        Args:
            conn_id: Primary key of the ConnectionProfile.
            table: Unquoted table name.
            pk_values: Mapping of PK column name → value.  Must exactly cover
                       the table's PK (partial PK → rejected, no unqualified DELETE).
            schema: Schema name; None means the engine default.

        Returns:
            True if a row was deleted, False if no row matched the given PK.

        Raises:
            RowEditError: If *pk_values* is empty, the table has no primary key,
                or *pk_values* keys don't exactly match the table's PK.
            UnknownIdentifierError: If *table* or any PK column is not in the
                live schema.
            ReadOnlyViolationError: If the connection is read-only.
            KeyError: If *conn_id* does not exist.
        """
        if not pk_values:
            raise RowEditError(
                "'pk_values' must not be empty; partial/unqualified DELETE is rejected."
            )

        adapter: DBAdapter = self._connection_manager.get_adapter(conn_id)

        # ── Whitelist table ──────────────────────────────────────────────
        known_tables: set[str] = {t.name for t in adapter.list_tables(schema)}
        quoted_table: str = adapter.validate_identifier(table, known=known_tables)

        # ── Fetch column + PK metadata ───────────────────────────────────
        table_schema = adapter.describe_table(table, schema)
        known_columns: set[str] = {c.name for c in table_schema.columns}
        pk_columns: list[str] = adapter.get_pk_columns(table, schema)

        if not pk_columns:
            raise RowEditError(f"Table {table!r} has no primary key; DELETE is not safe.")

        if set(pk_values.keys()) != set(pk_columns):
            raise RowEditError(
                f"pk_values keys {set(pk_values.keys())!r} must exactly match "
                f"the table's PK columns {set(pk_columns)!r}."
            )

        params: dict[str, object] = {}
        where_parts: list[str] = []

        for idx, (col, val) in enumerate(pk_values.items()):
            quoted_col = adapter.validate_identifier(col, known=known_columns)
            param_key = f"p{idx}"
            where_parts.append(f"{quoted_col} = :{param_key}")
            params[param_key] = val

        sql = f"DELETE FROM {quoted_table} WHERE {' AND '.join(where_parts)}"
        result = adapter.execute(sql, params)
        return result.row_count > 0

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _select_row_by_pk(
        self,
        adapter: DBAdapter,
        table: str,
        quoted_table: str,
        pk_values: dict[str, object],
        known_columns: set[str],
    ) -> dict[str, object]:
        """Re-SELECT a single row by PK and return it as a column→value dict.

        Args:
            adapter: Live DBAdapter instance.
            table: Unquoted table name (used only for error messages).
            quoted_table: Already-quoted table identifier for embedding in SQL.
            pk_values: Mapping of PK column → value.
            known_columns: Set of known-safe column names (already fetched from schema).

        Returns:
            Dict mapping each column name to its value, or ``{}`` if not found.
        """
        params: dict[str, object] = {}
        where_parts: list[str] = []

        for idx, (col, val) in enumerate(pk_values.items()):
            quoted_col = adapter.validate_identifier(col, known=known_columns)
            param_key = f"pk{idx}"
            where_parts.append(f"{quoted_col} = :{param_key}")
            params[param_key] = val

        sql = f"SELECT * FROM {quoted_table} WHERE {' AND '.join(where_parts)}"
        result = adapter.execute(sql, params)

        if not result.rows:
            return {}

        return dict(zip(result.columns, result.rows[0], strict=False))
