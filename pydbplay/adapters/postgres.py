"""PostgreSQL adapter using psycopg (sync mode)."""

# TODO(phase-1): Implement PostgreSQL adapter.
# Driver: psycopg[binary] (sync, NOT asyncpg).

from collections.abc import Iterator

from pydbplay.adapters.base import DBAdapter
from pydbplay.db.models import ConnectionProfile
from pydbplay.schemas.query import QueryResult
from pydbplay.schemas.schema import TableInfo, TableSchema


class PostgresAdapter(DBAdapter):
    """DBAdapter implementation for PostgreSQL via psycopg (sync).

    Uses psycopg sync connection — NOT asyncpg. FastAPI routes that call
    this adapter must be ``def`` (Starlette runs them in a threadpool).
    """

    def __init__(self, profile: ConnectionProfile) -> None:
        self._profile = profile
        # TODO(phase-1): initialise connection pool (max_size=5)

    def test_connection(self) -> bool:
        # TODO(phase-1): implement
        raise NotImplementedError

    def list_schemas(self) -> list[str]:
        # TODO(phase-1): implement — query information_schema.schemata
        raise NotImplementedError

    def list_tables(self, schema: str | None = None) -> list[TableInfo]:
        # TODO(phase-1): implement
        raise NotImplementedError

    def describe_table(self, table: str, schema: str | None = None) -> TableSchema:
        # TODO(phase-1): implement
        raise NotImplementedError

    def execute(self, sql: str, params: dict | None = None) -> QueryResult:
        # TODO(phase-1): implement — enforce read_only guard, parameterized queries
        raise NotImplementedError

    def execute_stream(self, sql: str, chunk_size: int = 1000) -> Iterator[list[dict]]:
        # TODO(phase-1): implement — use server-side cursor
        raise NotImplementedError

    def quote_identifier(self, name: str) -> str:
        # PostgreSQL uses double-quote: "name"
        # TODO(phase-1): implement
        raise NotImplementedError

    def validate_identifier(self, name: str, *, known: set[str]) -> str:
        # TODO(phase-1): implement whitelist check + quote_identifier
        raise NotImplementedError

    def get_pk_columns(self, table: str, schema: str | None = None) -> list[str]:
        # TODO(phase-1): implement — query pg_constraint
        raise NotImplementedError
