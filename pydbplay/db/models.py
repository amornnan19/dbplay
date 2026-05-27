"""Pydantic v2 models for app-internal data (ConnectionProfile, QueryHistory, SavedQuery).

These represent rows in the app's own SQLite database (~/.pydbplay/app.db).
They are separate from the user's target databases.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class ConnectionProfile(BaseModel):
    """Stored connection profile for a user's database."""

    id: int
    name: str
    """Human-friendly label, e.g. "Synora HR — local"."""

    engine: Literal["postgres", "mysql", "sqlite"]
    host: str | None = None
    port: int | None = None
    database: str
    username: str | None = None

    password_encrypted: str | None = None
    """Fernet-encrypted password (fallback). When keyring is used this is None
    and only a service/username reference is stored."""

    ssl_mode: str | None = None

    read_only: bool = False
    """Hard guard: adapter.execute() rejects non-SELECT/EXPLAIN when True.
    Primary use case: connecting to production databases. (Phase 1)"""

    color: str | None = None
    """Hex colour for the tab indicator, e.g. "#6366f1". (Phase 4)"""

    created_at: datetime
    updated_at: datetime
    last_used_at: datetime | None = None


class QueryHistory(BaseModel):
    """Record of a single query execution."""

    id: int
    connection_id: int
    sql: str
    executed_at: datetime
    duration_ms: int
    row_count: int | None = None
    success: bool
    error_message: str | None = None


class SavedQuery(BaseModel):
    """A user-saved (named) SQL query."""

    id: int
    connection_id: int | None = None
    """None means the query is global (not tied to a specific connection)."""

    name: str
    sql: str
    description: str | None = None
    created_at: datetime
    updated_at: datetime
