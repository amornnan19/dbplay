"""SQLite adapter using stdlib sqlite3."""

# TODO(phase-1): Implement SQLite adapter.
# Driver: stdlib sqlite3 (sync — no aiosqlite).

from collections.abc import Iterator

from pydbplay.adapters.base import DBAdapter
from pydbplay.db.models import ConnectionProfile
from pydbplay.schemas.query import QueryResult
from pydbplay.schemas.schema import TableInfo, TableSchema


class SQLiteAdapter(DBAdapter):
    """DBAdapter implementation for SQLite via stdlib sqlite3.

    SQLite adapter is the simplest — implement first to validate the
    DBAdapter interface before tackling Postgres/MySQL. (SPEC §10 order)

    In-memory databases (``database=":memory:"``) are used in unit tests.
    """

    def __init__(self, profile: ConnectionProfile) -> None:
        self._profile = profile
        # TODO(phase-1): open sqlite3.connect(profile.database)

    def test_connection(self) -> bool:
        # TODO(phase-1): implement
        raise NotImplementedError

    def list_schemas(self) -> list[str]:
        # SQLite has no schemas — return ["main"]
        # TODO(phase-1): implement
        raise NotImplementedError

    def list_tables(self, schema: str | None = None) -> list[TableInfo]:
        # TODO(phase-1): query sqlite_master
        raise NotImplementedError

    def describe_table(self, table: str, schema: str | None = None) -> TableSchema:
        # TODO(phase-1): PRAGMA table_info + PRAGMA index_list
        raise NotImplementedError

    def execute(self, sql: str, params: dict | None = None) -> QueryResult:
        # TODO(phase-1): implement — enforce read_only guard, parameterized queries
        raise NotImplementedError

    def execute_stream(self, sql: str, chunk_size: int = 1000) -> Iterator[list[dict]]:
        # TODO(phase-1): implement — fetchmany(chunk_size) loop
        raise NotImplementedError

    def quote_identifier(self, name: str) -> str:
        # SQLite uses double-quote: "name"
        # TODO(phase-1): implement
        raise NotImplementedError

    def validate_identifier(self, name: str, *, known: set[str]) -> str:
        # TODO(phase-1): implement whitelist check + quote_identifier
        raise NotImplementedError

    def get_pk_columns(self, table: str, schema: str | None = None) -> list[str]:
        # TODO(phase-1): PRAGMA table_info → pk column
        raise NotImplementedError
