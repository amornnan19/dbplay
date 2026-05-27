"""Custom exceptions and FastAPI exception-handler registration."""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse


class PydbplayError(Exception):
    """Base error for pydbplay application layer."""


class ConnectionNotFound(PydbplayError):
    """Raised when a requested connection profile does not exist."""

    def __init__(self, connection_id: int) -> None:
        super().__init__(f"Connection {connection_id} not found.")
        self.connection_id = connection_id


class AdapterError(PydbplayError):
    """Raised when a DB adapter operation fails."""


class ReadOnlyViolation(PydbplayError):
    """Raised when a non-SELECT statement is executed on a read-only connection."""


def register_exception_handlers(app: FastAPI) -> None:
    """Attach custom exception handlers to *app*."""

    @app.exception_handler(ConnectionNotFound)
    async def _connection_not_found(
        request: Request, exc: ConnectionNotFound
    ) -> JSONResponse:
        return JSONResponse(
            status_code=404,
            content={"detail": str(exc)},
        )

    @app.exception_handler(AdapterError)
    async def _adapter_error(request: Request, exc: AdapterError) -> JSONResponse:
        return JSONResponse(
            status_code=502,
            content={"detail": str(exc)},
        )

    @app.exception_handler(ReadOnlyViolation)
    async def _read_only_violation(
        request: Request, exc: ReadOnlyViolation
    ) -> JSONResponse:
        return JSONResponse(
            status_code=403,
            content={"detail": str(exc)},
        )
