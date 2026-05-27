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
