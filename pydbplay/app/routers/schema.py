"""API routes for schema inspection (tables, columns, indexes)."""

# Endpoints per SPEC §6:
#   GET /api/c/{conn_id}/schemas              → schema_select partial (HTML)
#   GET /api/c/{conn_id}/tables?schema=X      → table_list partial (HTML)
#   GET /api/c/{conn_id}/tables/{table}       → table_structure partial (HTML)

from pathlib import Path

from fastapi import APIRouter, Query
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from starlette.requests import Request

from pydbplay.app.dependencies import ConnectionManagerDep, RepositoryDep
from pydbplay.app.exceptions import ConnectionNotFound

_TEMPLATES_DIR = Path(__file__).parent.parent / "templates"
_templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

router = APIRouter(prefix="/api/c", tags=["schema"])


@router.get("/{conn_id}/schemas", response_class=HTMLResponse)
def list_schemas(
    conn_id: int,
    request: Request,
    repository: RepositoryDep,
    connection_manager: ConnectionManagerDep,
) -> HTMLResponse:
    """GET /api/c/{conn_id}/schemas → schema_select partial."""
    profile = repository.get_connection(conn_id)
    if profile is None:
        raise ConnectionNotFound(conn_id)
    # Let UnsupportedEngineError propagate → 422 via global handler
    adapter = connection_manager.get_adapter(conn_id)
    schemas = adapter.list_schemas()
    return _templates.TemplateResponse(
        request,
        "partials/schema_select.html",
        {"schemas": schemas, "conn_id": conn_id},
    )


@router.get("/{conn_id}/tables", response_class=HTMLResponse)
def list_tables(
    conn_id: int,
    request: Request,
    repository: RepositoryDep,
    connection_manager: ConnectionManagerDep,
    schema: str | None = Query(default=None),
) -> HTMLResponse:
    """GET /api/c/{conn_id}/tables?schema=X → table_list partial."""
    profile = repository.get_connection(conn_id)
    if profile is None:
        raise ConnectionNotFound(conn_id)
    # Let UnsupportedEngineError propagate → 422 via global handler
    adapter = connection_manager.get_adapter(conn_id)
    tables = adapter.list_tables(schema)
    # Build server-side SELECT snippets using quote_identifier so the SQL is
    # correctly quoted and never interpolated from untrusted input.
    table_rows = [
        {
            "info": t,
            "select_sql": (
                f"SELECT * FROM {adapter.quote_identifier(t.name)} LIMIT 100"
            ),
        }
        for t in tables
    ]
    return _templates.TemplateResponse(
        request,
        "partials/table_list.html",
        {
            "table_rows": table_rows,
            "conn_id": conn_id,
            "schema": schema,
        },
    )


@router.get("/{conn_id}/tables/{table}", response_class=HTMLResponse)
def describe_table(
    conn_id: int,
    table: str,
    request: Request,
    repository: RepositoryDep,
    connection_manager: ConnectionManagerDep,
    schema: str | None = Query(default=None),
) -> HTMLResponse:
    """GET /api/c/{conn_id}/tables/{table}?schema=X → table_structure partial."""
    profile = repository.get_connection(conn_id)
    if profile is None:
        raise ConnectionNotFound(conn_id)
    # Let UnsupportedEngineError propagate → 422 via global handler
    adapter = connection_manager.get_adapter(conn_id)
    # table name comes from the path; adapter.describe_table uses PRAGMA with
    # quote_identifier internally — no raw SQL built from path param here.
    table_schema = adapter.describe_table(table, schema)
    return _templates.TemplateResponse(
        request,
        "partials/table_structure.html",
        {"table_schema": table_schema},
    )
