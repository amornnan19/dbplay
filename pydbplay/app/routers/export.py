"""API routes for data export (Phase 3).

Endpoint:
  POST /api/c/{conn_id}/export?format=csv|json|sql  → file download stream

The endpoint is NOT an HTMX-swap target — it returns a StreamingResponse so
the browser pipes the data directly to disk as a file download.

CSRF: the JS helper sends the token as a hidden form field (``csrf_token``),
which the security middleware accepts as a fallback to the X-CSRFToken header.
"""

import re
from collections.abc import Iterator
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Form, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.exc import SQLAlchemyError
from starlette.requests import Request

from pydbplay.adapters.base import AdapterError, UnknownIdentifierError
from pydbplay.app.dependencies import ConnectionManagerDep, ExporterDep, RepositoryDep
from pydbplay.app.exceptions import ConnectionNotFound
from pydbplay.core.exporter import ExporterError, ExportFormat, ReadOnlyViolationError

router = APIRouter(prefix="/api/c", tags=["export"])

# Media types per format
_MEDIA_TYPE: dict[str, str] = {
    "csv": "text/csv",
    "json": "application/json",
    "sql": "application/sql",
}

_VALID_FORMATS = frozenset(_MEDIA_TYPE.keys())


@router.post("/{conn_id}/export")
def export_data(
    conn_id: int,
    request: Request,
    repository: RepositoryDep,
    connection_manager: ConnectionManagerDep,
    exporter: ExporterDep,
    fmt: Annotated[str, Query(alias="format")] = "csv",
    sql: Annotated[str | None, Form()] = None,
    table: Annotated[str | None, Form()] = None,
) -> StreamingResponse:
    """POST /api/c/{conn_id}/export?format=csv|json|sql → streaming file download.

    Exactly one of ``sql`` or ``table`` form fields must be provided:
      - ``sql``   — export the result of an arbitrary SELECT query.
      - ``table`` — export all rows from a named table (identifier-whitelisted).

    The response is a ``StreamingResponse`` — the browser downloads it directly
    to disk.  This endpoint is NOT an HTMX partial.
    """
    # Validate format
    if fmt not in _VALID_FORMATS:
        raise HTTPException(
            status_code=400, detail=f"Unknown format {fmt!r}. Must be csv, json, or sql."
        )

    # Resolve profile — 404 if missing
    if repository.get_connection(conn_id) is None:
        raise ConnectionNotFound(conn_id)

    # Exactly one of sql / table must be provided
    if sql is None and table is None:
        raise HTTPException(
            status_code=400,
            detail="Provide exactly one of 'sql' or 'table' form fields.",
        )
    if sql is not None and table is not None:
        raise HTTPException(
            status_code=400,
            detail="Provide exactly one of 'sql' or 'table' form fields, not both.",
        )

    # Cast is safe — mypy can't narrow Literal from a runtime str check
    export_format: ExportFormat = fmt  # type: ignore[assignment]

    # Build the generator and raw filename
    generator: Iterator[str]
    if sql is not None:
        raw_name = f"query_result.{fmt}"
        generator = exporter.export_query(conn_id, sql, export_format)
    else:
        # table is not None here (guaranteed by the checks above)
        assert table is not None
        # Eagerly validate the table identifier BEFORE returning StreamingResponse.
        # If validation raises UnknownIdentifierError here (before any headers are
        # sent) FastAPI's exception handler can still produce a proper 400 response.
        # (Errors inside a StreamingResponse generator fire after headers are sent
        # and cannot change the status code.)
        adapter = connection_manager.get_adapter(conn_id)
        known_tables = {t.name for t in adapter.list_tables(None)}
        try:
            adapter.validate_identifier(table, known=known_tables)
        except UnknownIdentifierError:
            raise  # caught by the registered exception handler → 400
        raw_name = f"{table}.{fmt}"
        generator = exporter.export_table(conn_id, table, export_format)

    # Prime the generator so that execution errors (bad SQL, non-SELECT on
    # read-only, DB-level failures) surface HERE — before headers are committed
    # — and can be returned as a proper 400 rather than a silent empty download.
    first_chunk: str | None
    try:
        first_chunk = next(generator)
    except StopIteration:
        first_chunk = None
    except (
        ExporterError,
        UnknownIdentifierError,
        ReadOnlyViolationError,
        AdapterError,
        SQLAlchemyError,
    ) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    def _primed() -> Iterator[str]:
        if first_chunk is not None:
            yield first_chunk
        yield from generator

    # Sanitize filename for Content-Disposition (RFC 5987):
    # - ascii_name strips any chars that could break the quoted-string token
    # - filename* carries the full UTF-8 name for clients that support it
    ascii_name = re.sub(r"[^A-Za-z0-9._-]", "_", raw_name)
    disposition = f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(raw_name)}"

    media_type = _MEDIA_TYPE[fmt]
    headers = {"Content-Disposition": disposition}
    return StreamingResponse(_primed(), media_type=media_type, headers=headers)
