"""Pydantic schemas for connection profile API endpoints."""

from typing import Literal

from pydantic import BaseModel, Field


class ConnectionCreate(BaseModel):
    """Request body for POST /api/connections."""

    name: str = Field(..., min_length=1, max_length=200)
    engine: Literal["postgres", "mysql", "sqlite"]
    host: str | None = None
    port: int | None = Field(None, gt=0, lt=65536)
    database: str
    username: str | None = None
    password: str | None = None
    """Plaintext password — stored via keyring/Fernet, never persisted as-is."""
    ssl_mode: str | None = None
    read_only: bool = False
    color: str | None = None


class ConnectionUpdate(BaseModel):
    """Request body for PATCH /api/connections/{id}.

    All fields optional — only provided fields are updated.
    """

    name: str | None = Field(None, min_length=1, max_length=200)
    host: str | None = None
    port: int | None = Field(None, gt=0, lt=65536)
    database: str | None = None
    username: str | None = None
    password: str | None = None
    ssl_mode: str | None = None
    read_only: bool | None = None
    color: str | None = None


class SavedQueryUpdate(BaseModel):
    """Payload for partially updating a saved query.

    All fields optional — only provided fields are updated.
    Pass ``description=None`` explicitly to clear the column.
    """

    name: str | None = Field(None, min_length=1, max_length=200)
    sql: str | None = None
    description: str | None = None


class ConnectionTestResult(BaseModel):
    """Result of a connection test (transient, not persisted)."""

    ok: bool
    message: str


class ConnectionResponse(BaseModel):
    """Response schema for a single connection profile (no password fields)."""

    id: int
    name: str
    engine: Literal["postgres", "mysql", "sqlite"]
    host: str | None = None
    port: int | None = None
    database: str
    username: str | None = None
    ssl_mode: str | None = None
    read_only: bool
    color: str | None = None
