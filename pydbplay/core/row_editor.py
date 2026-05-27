"""Row-level CRUD operations with identifier safety and transaction wrapping."""

# TODO(phase-2): Implement row editor.
# Per SPEC §9 Identifier safety: whitelist every table/column/schema name
# against the live schema BEFORE quoting. Values always go through parameterized queries.

from pydbplay.adapters.base import DBAdapter
from pydbplay.schemas.query import QueryResult


class RowEditor:
    """Provides INSERT / UPDATE / DELETE operations on a single table.

    Every mutating operation is wrapped in a transaction and rolled back on error
    (pairs with the optimistic-UI rollback in the frontend).

    Identifier safety (SPEC §9):
    1. Whitelist table/column/schema against ``describe_table`` results.
    2. Then call ``adapter.quote_identifier``.
    3. Values always use parameterized queries.
    """

    def __init__(self, adapter: DBAdapter) -> None:
        self._adapter = adapter

    def update_row(
        self,
        table: str,
        pk_values: dict[str, object],
        updates: dict[str, object],
        schema: str | None = None,
    ) -> QueryResult:
        """Update a single row identified by *pk_values*.

        Args:
            table: Unquoted table name.
            pk_values: Mapping of PK column name → value.
            updates: Mapping of column name → new value.
            schema: Schema name; None means DB default.

        Returns:
            QueryResult containing the updated row.
        """
        # TODO(phase-2): implement
        raise NotImplementedError

    def insert_row(
        self,
        table: str,
        values: dict[str, object],
        schema: str | None = None,
    ) -> QueryResult:
        """Insert a new row into *table*.

        Args:
            table: Unquoted table name.
            values: Mapping of column name → value.
            schema: Schema name; None means DB default.

        Returns:
            QueryResult containing the inserted row.
        """
        # TODO(phase-2): implement
        raise NotImplementedError

    def delete_row(
        self,
        table: str,
        pk_values: dict[str, object],
        schema: str | None = None,
    ) -> None:
        """Delete the row identified by *pk_values*.

        Args:
            table: Unquoted table name.
            pk_values: Mapping of PK column name → value.
            schema: Schema name; None means DB default.
        """
        # TODO(phase-2): implement
        raise NotImplementedError
