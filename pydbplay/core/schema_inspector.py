"""Retrieves and caches schema metadata (schemas, tables, columns, indexes)."""

# TODO(phase-1): Implement schema inspection with optional caching.

from pydbplay.adapters.base import DBAdapter
from pydbplay.schemas.schema import TableInfo, TableSchema


class SchemaInspector:
    """Wraps a DBAdapter to provide schema metadata, with optional caching.

    The inspector is per-connection; cache lifetime is intentionally short
    (seconds to minutes) since schemas may change during a dev session.
    """

    def __init__(self, adapter: DBAdapter) -> None:
        self._adapter = adapter

    def list_schemas(self) -> list[str]:
        """Return all schema names visible to the connected user.

        Returns:
            Sorted list of schema name strings.
        """
        # TODO(phase-1): delegate to adapter + cache
        raise NotImplementedError

    def list_tables(self, schema: str | None = None) -> list[TableInfo]:
        """Return tables in *schema* (or the default schema if None).

        Args:
            schema: Schema name filter; None means DB default.

        Returns:
            List of TableInfo objects.
        """
        # TODO(phase-1): delegate to adapter + cache
        raise NotImplementedError

    def describe_table(self, table: str, schema: str | None = None) -> TableSchema:
        """Return detailed metadata for *table*.

        Args:
            table: Unquoted table name.
            schema: Schema name; None means DB default.

        Returns:
            TableSchema with columns, indexes, and foreign keys.
        """
        # TODO(phase-1): delegate to adapter + cache
        raise NotImplementedError
