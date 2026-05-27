"""Pydantic schemas for row-level browse and edit endpoints (Phase 2)."""

from typing import Any

from pydantic import BaseModel


class BrowseResult(BaseModel):
    """Result of a row-browse operation against a single table.

    The page_size+1 trick is used internally: the query fetches ``page_size + 1``
    rows; if more than ``page_size`` are returned then ``has_next=True`` and the
    extra row is dropped before this model is constructed.  This avoids an
    expensive ``COUNT(*)`` on large production tables (SPEC §9).
    """

    table: str
    """Unquoted table name that was browsed."""

    schema_name: str | None = None
    """DB schema that owns *table*, or None for the engine default."""

    columns: list[str]
    """Ordered list of column names."""

    rows: list[list[Any]]
    """Row data as nested lists, aligned with *columns*.  At most *page_size* rows."""

    page: int
    """1-based current page number."""

    page_size: int
    """Maximum rows per page."""

    has_next: bool
    """True when at least one more page exists beyond *page*."""

    pk_columns: list[str]
    """Ordered list of primary-key column names for the table."""
