"""API routes for row-level CRUD (Phase 2).

Endpoints per SPEC §6:
  GET    /api/c/{conn_id}/rows/{table}   → row_grid partial (HTML)
  PATCH  /api/c/{conn_id}/rows/{table}   → updated row partial (HTML)
  POST   /api/c/{conn_id}/rows/{table}   → refreshed row_grid partial (HTML)
  DELETE /api/c/{conn_id}/rows/{table}   → 204 (HTMX removes <tr>)

PK is always passed as form fields ``pk_<colname>=<value>`` (composite-PK safe).
Filter params use the ``f_<col>=<value>`` convention.
"""

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.templating import Jinja2Templates
from starlette.datastructures import FormData

from pydbplay.adapters.base import AdapterError, ReadOnlyViolationError, UnknownIdentifierError
from pydbplay.app.dependencies import RepositoryDep, RowEditorDep
from pydbplay.app.exceptions import ConnectionNotFound
from pydbplay.core.row_editor import RowEditError

_TEMPLATES_DIR = Path(__file__).parent.parent / "templates"
_templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

router = APIRouter(prefix="/api/c", tags=["rows"])

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_PK_PREFIX = "pk_"
_FILTER_PREFIX = "f_"


def _error_partial(request: Request, message: str, status_code: int = 400) -> HTMLResponse:
    """Render a small inline error partial (200-or-4xx, never 500)."""
    return _templates.TemplateResponse(
        request,
        "partials/row_error.html",
        {"error": message},
        status_code=status_code,
    )


def _extract_pk_values(form: FormData) -> dict[str, object]:
    """Pull all ``pk_<col>=<val>`` entries from the form into a plain dict."""
    return {key[len(_PK_PREFIX) :]: str(form[key]) for key in form if key.startswith(_PK_PREFIX)}


# ---------------------------------------------------------------------------
# GET /api/c/{conn_id}/rows/{table}
# ---------------------------------------------------------------------------


@router.get("/{conn_id}/rows/{table}", response_class=HTMLResponse)
def browse_rows(
    conn_id: int,
    table: str,
    request: Request,
    repository: RepositoryDep,
    row_editor: RowEditorDep,
    page: int = 1,
    page_size: int = 50,
    sort: str | None = None,
    dir: str = "ASC",
) -> HTMLResponse:
    """GET /api/c/{conn_id}/rows/{table} → row_grid partial.

    Query params:
      - page, page_size  — pagination
      - sort, dir        — column sort (dir must be ASC or DESC)
      - f_<col>=<value>  — per-column equality filters
    """
    if repository.get_connection(conn_id) is None:
        raise ConnectionNotFound(conn_id)

    # Collect f_<col>=<val> filter params from the request query string
    filters: list[tuple[str, object]] | None = None
    raw_filters: list[tuple[str, str]] = [
        (key[len(_FILTER_PREFIX) :], str(val))
        for key, val in request.query_params.items()
        if key.startswith(_FILTER_PREFIX) and val
    ]
    if raw_filters:
        filters = [(col, val) for col, val in raw_filters]

    # Build sort tuple
    sort_tuple: tuple[str, str] | None = None
    if sort:
        dir_upper = dir.upper() if dir else "ASC"
        if dir_upper not in {"ASC", "DESC"}:
            dir_upper = "ASC"
        sort_tuple = (sort, dir_upper)

    try:
        result = row_editor.browse(
            conn_id,
            table,
            page=page,
            page_size=page_size,
            filters=filters,
            sort=sort_tuple,
        )
    except (RowEditError, UnknownIdentifierError, AdapterError) as exc:
        return _error_partial(request, str(exc), status_code=400)

    return _templates.TemplateResponse(
        request,
        "partials/row_grid.html",
        {
            "result": result,
            "conn_id": conn_id,
            "table": table,
            "page": page,
            "page_size": page_size,
            "sort": sort,
            "dir": dir.upper() if dir else "ASC",
            "filters": dict(raw_filters) if raw_filters else {},
            "error": None,
        },
    )


# ---------------------------------------------------------------------------
# PATCH /api/c/{conn_id}/rows/{table}
# ---------------------------------------------------------------------------


@router.patch("/{conn_id}/rows/{table}", response_class=HTMLResponse)
async def update_row(
    conn_id: int,
    table: str,
    request: Request,
    repository: RepositoryDep,
    row_editor: RowEditorDep,
) -> HTMLResponse:
    """PATCH /api/c/{conn_id}/rows/{table} → updated <tr> partial.

    Form fields:
      - column: the column being edited
      - value: the new value
      - pk_<colname>: one field per PK column
    """
    if repository.get_connection(conn_id) is None:
        raise ConnectionNotFound(conn_id)

    form = await request.form()
    column = str(form.get("column", "")).strip()
    value: object = form.get("value", "")
    # Treat empty string as None (NULL) for non-string use-cases?
    # Keep as str — adapter + DB handles coercion; user can enter NULL explicitly.

    pk_values = _extract_pk_values(form)

    if not column:
        return _error_partial(request, "Missing 'column' form field.")
    if not pk_values:
        return _error_partial(request, "Missing pk_* form fields to identify the row.")

    try:
        updated = row_editor.update_row(conn_id, table, pk_values, {column: value})
    except ReadOnlyViolationError as exc:
        return _error_partial(request, str(exc), status_code=403)
    except (RowEditError, UnknownIdentifierError, AdapterError) as exc:
        return _error_partial(request, str(exc), status_code=400)

    if not updated:
        return _error_partial(
            request,
            "Row not found (it may have been deleted or changed by another session).",
            status_code=404,
        )

    # Build pk_obj for the delete button (same pattern as row_grid.html)
    pk_obj = {f"pk_{k}": v for k, v in pk_values.items()}

    return _templates.TemplateResponse(
        request,
        "partials/row.html",
        {
            "columns": list(updated.keys()),
            "row": list(updated.values()),
            "pk_columns": list(pk_values.keys()),
            "pk_values": pk_values,
            "pk_obj": pk_obj,
            "conn_id": conn_id,
            "table": table,
        },
    )


# ---------------------------------------------------------------------------
# POST /api/c/{conn_id}/rows/{table}
# ---------------------------------------------------------------------------


@router.post("/{conn_id}/rows/{table}", response_class=HTMLResponse)
async def insert_row(
    conn_id: int,
    table: str,
    request: Request,
    repository: RepositoryDep,
    row_editor: RowEditorDep,
) -> HTMLResponse:
    """POST /api/c/{conn_id}/rows/{table} → refreshed row_grid partial.

    Form fields: column values keyed by column name (no pk_ prefix).
    After insert, re-browse page 1 so the new row appears.
    """
    if repository.get_connection(conn_id) is None:
        raise ConnectionNotFound(conn_id)

    form = await request.form()
    # Collect all form fields except CSRF token; skip empty values
    values: dict[str, object] = {
        key: str(val)
        for key, val in form.items()
        if not key.startswith("_") and key != "csrf_token" and str(val).strip()
    }

    if not values:
        return _error_partial(request, "No column values provided for insert.")

    try:
        row_editor.insert_row(conn_id, table, values)
    except ReadOnlyViolationError as exc:
        return _error_partial(request, str(exc), status_code=403)
    except (RowEditError, UnknownIdentifierError, AdapterError) as exc:
        return _error_partial(request, str(exc), status_code=400)

    # Re-browse page 1 to show the updated grid with the new row
    try:
        result = row_editor.browse(conn_id, table, page=1, page_size=50)
    except (RowEditError, UnknownIdentifierError, AdapterError) as exc:
        return _error_partial(request, str(exc), status_code=400)

    return _templates.TemplateResponse(
        request,
        "partials/row_grid.html",
        {
            "result": result,
            "conn_id": conn_id,
            "table": table,
            "page": 1,
            "page_size": 50,
            "sort": None,
            "dir": "ASC",
            "filters": {},
            "error": None,
        },
    )


# ---------------------------------------------------------------------------
# DELETE /api/c/{conn_id}/rows/{table}
# ---------------------------------------------------------------------------


@router.delete("/{conn_id}/rows/{table}")
async def delete_row(
    conn_id: int,
    table: str,
    request: Request,
    repository: RepositoryDep,
    row_editor: RowEditorDep,
) -> Response:
    """DELETE /api/c/{conn_id}/rows/{table} → 204 (HTMX removes the <tr>).

    Form fields: pk_<colname>=<value> for each PK column.
    Returns 204 on success; error partial on failure.
    """
    if repository.get_connection(conn_id) is None:
        raise ConnectionNotFound(conn_id)

    form = await request.form()
    pk_values = _extract_pk_values(form)

    if not pk_values:
        return _error_partial(request, "Missing pk_* form fields to identify the row.")

    try:
        row_editor.delete_row(conn_id, table, pk_values)
    except ReadOnlyViolationError as exc:
        return _error_partial(request, str(exc), status_code=403)
    except (RowEditError, UnknownIdentifierError, AdapterError) as exc:
        return _error_partial(request, str(exc), status_code=400)

    return Response(status_code=204)
