"""FastAPI dependency-injection stubs."""

# TODO(phase-1): Implement real DI — wire ConnectionManager to the app-internal
#                SQLite repo and inject it into routers.

from typing import Annotated

from fastapi import Depends


class ConnectionManager:
    """Stub placeholder — see pydbplay/core/connection_manager.py."""

    # TODO(phase-1): Replace with real ConnectionManager from core/


def _get_connection_manager() -> ConnectionManager:
    """Provide a ConnectionManager instance (stub)."""
    # TODO(phase-1): Instantiate from Settings / repo
    return ConnectionManager()


ConnectionManagerDep = Annotated[ConnectionManager, Depends(_get_connection_manager)]
