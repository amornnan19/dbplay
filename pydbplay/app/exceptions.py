"""Custom exceptions and FastAPI exception-handler registration."""

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse

from pydbplay.adapters.base import AdapterError as _AdapterError
from pydbplay.adapters.base import UnsupportedEngineError as _UnsupportedEngineError


class PydbplayError(Exception):
    """Base error for pydbplay application layer."""


class ConnectionNotFound(PydbplayError):
    """Raised when a requested connection profile does not exist."""

    def __init__(self, connection_id: int) -> None:
        super().__init__(f"Connection {connection_id} not found.")
        self.connection_id = connection_id


class ReadOnlyViolation(PydbplayError):
    """Raised when a non-SELECT statement is executed on a read-only connection."""


def _is_htmx(request: Request) -> bool:
    """Return True when the request carries the HTMX header."""
    return request.headers.get("HX-Request") == "true"


def _error_html(message: str) -> str:
    return (
        f'<div class="rounded p-3 bg-red-50 border border-red-200 text-red-700 text-sm">'
        f"{message}</div>"
    )


def register_exception_handlers(app: FastAPI) -> None:
    """Attach custom exception handlers to *app*."""

    @app.exception_handler(ConnectionNotFound)
    async def _connection_not_found(
        request: Request, exc: ConnectionNotFound
    ) -> HTMLResponse | JSONResponse:
        if _is_htmx(request):
            return HTMLResponse(content=_error_html(str(exc)), status_code=404)
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @app.exception_handler(ReadOnlyViolation)
    async def _read_only_violation(
        request: Request, exc: ReadOnlyViolation
    ) -> HTMLResponse | JSONResponse:
        if _is_htmx(request):
            return HTMLResponse(content=_error_html(str(exc)), status_code=403)
        return JSONResponse(status_code=403, content={"detail": str(exc)})

    # ── Adapter-layer exceptions (raised from core/) ──────────────────────

    @app.exception_handler(_UnsupportedEngineError)
    async def _unsupported_engine(
        request: Request, exc: _UnsupportedEngineError
    ) -> HTMLResponse | JSONResponse:
        msg = str(exc)
        if _is_htmx(request):
            return HTMLResponse(content=_error_html(msg), status_code=422)
        return JSONResponse(status_code=422, content={"detail": msg})

    @app.exception_handler(_AdapterError)
    async def _base_adapter_error(
        request: Request, exc: _AdapterError
    ) -> HTMLResponse | JSONResponse:
        msg = str(exc)
        if _is_htmx(request):
            return HTMLResponse(content=_error_html(msg), status_code=400)
        return JSONResponse(status_code=400, content={"detail": msg})
