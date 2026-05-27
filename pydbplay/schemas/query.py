"""Pydantic schemas for query execution API endpoints."""

from typing import Any

from pydantic import BaseModel


class QueryResult(BaseModel):
    """Result of a single query execution.

    Used by DBAdapter.execute() and returned from /api/c/{conn_id}/query.
    """

    columns: list[str]
    """Ordered list of column names."""

    rows: list[list[Any]]
    """Row data as nested lists, aligned with *columns*."""

    row_count: int
    """Number of rows in *rows* (for SELECT) or rows affected (for DML)."""

    duration_ms: int
    """Wall-clock execution time in milliseconds."""

    truncated: bool = False
    """True when a LIMIT was auto-injected and the full result would be larger."""


class QueryRunResult(BaseModel):
    """Result returned by ``QueryExecutor.run()``.

    Extends the fields of ``QueryResult`` with executor-level metadata such as
    the effective SQL actually sent to the DB, whether a LIMIT was injected,
    and whether the query is considered destructive.
    """

    columns: list[str]
    """Ordered list of column names (empty for DML that returns no rows)."""

    rows: list[list[Any]]
    """Row data as nested lists, aligned with *columns*."""

    row_count: int
    """Rows returned (SELECT) or rows affected (DML)."""

    duration_ms: int
    """Wall-clock execution time in milliseconds (recorded by the executor)."""

    effective_sql: str
    """The SQL string actually sent to the database (may include injected LIMIT)."""

    limit_applied: bool = False
    """True when the executor injected a LIMIT clause that was not in the original SQL."""

    truncated_possible: bool = False
    """True when limit_applied=True — the full result set may be larger than returned."""

    is_destructive: bool = False
    """True when the original SQL is DELETE / UPDATE / DROP / TRUNCATE."""


class QueryRequest(BaseModel):
    """Request body for POST /api/c/{conn_id}/query."""

    sql: str
    limit: int | None = 1000
    """Override auto-LIMIT; set to None to opt out (large result confirmation required)."""


class ValidateRequest(BaseModel):
    """Request body for POST /api/c/{conn_id}/query/validate."""

    sql: str
    dialect: str = ""
    """sqlglot dialect: "postgres", "mysql", "sqlite", or "" for generic SQL."""


class ValidateResponse(BaseModel):
    """Response for POST /api/c/{conn_id}/query/validate."""

    ok: bool
    is_destructive: bool = False
    errors: list[str] = []
    dialect: str = ""


class Suggestion(BaseModel):
    """A single autocomplete suggestion for the SQL editor."""

    label: str
    """Display text (table name, column name, keyword)."""

    kind: str
    """One of: "table", "column", "schema", "keyword"."""

    detail: str | None = None
    """Extra detail shown in the autocomplete popup (e.g. column type)."""
