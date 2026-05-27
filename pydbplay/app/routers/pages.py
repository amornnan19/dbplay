"""HTML page routes rendered via Jinja2."""

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from pydbplay.app.dependencies import RepositoryDep

_TEMPLATES_DIR = Path(__file__).parent.parent / "templates"

router = APIRouter(tags=["pages"])

_templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))


@router.get("/", include_in_schema=False)
def root_redirect() -> RedirectResponse:
    """Redirect / → /connections."""
    return RedirectResponse(url="/connections", status_code=302)


@router.get("/connections", response_class=HTMLResponse)
def connections_page(request: Request) -> HTMLResponse:
    """Render the connection-list page."""
    return _templates.TemplateResponse(
        request,
        "connections.html",
        {"connections": []},  # TODO(phase-1): load real connection profiles
    )


@router.get("/c/{conn_id}", response_class=HTMLResponse)
def workspace_page(conn_id: int, request: Request, repository: RepositoryDep) -> HTMLResponse:
    """GET /c/{conn_id} → query workspace page (404 if connection not found)."""
    conn = repository.get_connection(conn_id)
    if conn is None:
        return _templates.TemplateResponse(
            request,
            "partials/connection_error.html",
            {"message": f"Connection {conn_id} not found."},
            status_code=404,
        )
    return _templates.TemplateResponse(
        request,
        "query.html",
        {"conn": conn, "conn_id": conn_id, "dialect": conn.engine},
    )


@router.get("/c/{conn_id}/query", response_class=HTMLResponse)
def workspace_query_page(conn_id: int, request: Request, repository: RepositoryDep) -> HTMLResponse:
    """GET /c/{conn_id}/query → same query workspace (alias)."""
    conn = repository.get_connection(conn_id)
    if conn is None:
        return _templates.TemplateResponse(
            request,
            "partials/connection_error.html",
            {"message": f"Connection {conn_id} not found."},
            status_code=404,
        )
    return _templates.TemplateResponse(
        request,
        "query.html",
        {"conn": conn, "conn_id": conn_id, "dialect": conn.engine},
    )
