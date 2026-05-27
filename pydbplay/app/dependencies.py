"""FastAPI dependency-injection helpers — read from app.state."""

from typing import Annotated

from fastapi import Depends, Request

from pydbplay.core.connection_manager import ConnectionManager
from pydbplay.db.repository import Repository


def get_repository(request: Request) -> Repository:
    """Provide the app-internal Repository from app.state."""
    return request.app.state.repository  # type: ignore[no-any-return]


def get_connection_manager(request: Request) -> ConnectionManager:
    """Provide the ConnectionManager from app.state."""
    return request.app.state.connection_manager  # type: ignore[no-any-return]


RepositoryDep = Annotated[Repository, Depends(get_repository)]
ConnectionManagerDep = Annotated[ConnectionManager, Depends(get_connection_manager)]
