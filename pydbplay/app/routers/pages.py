"""HTML page routes rendered via Jinja2."""

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from pydbplay.app.dependencies import RepositoryDep
from pydbplay.db.repository import Repository

_TEMPLATES_DIR = Path(__file__).parent.parent / "templates"

router = APIRouter(tags=["pages"])

_templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))


@router.get("/", include_in_schema=False)
def root_redirect() -> RedirectResponse:
    """Redirect / → /connections."""
    return RedirectResponse(url="/connections", status_code=302)


def _profiles_as_json(repository: Repository) -> list[dict[str, object]]:
    """Return list_connections() serialized to plain dicts (JSON-safe for tojson).

    Only the fields needed by the tab bar are included — sensitive fields such
    as password_encrypted, host, port, username, and database are intentionally
    omitted.
    """
    return [
        {
            "id": p.id,
            "name": p.name,
            "engine": p.engine,
            "color": p.color,
            "last_used_at": p.last_used_at.isoformat() if p.last_used_at else None,
        }
        for p in repository.list_connections()
    ]


@router.get("/connections", response_class=HTMLResponse)
def connections_page(request: Request, repository: RepositoryDep) -> HTMLResponse:
    """Render the connection-list page."""
    return _templates.TemplateResponse(
        request,
        "connections.html",
        {"profiles": _profiles_as_json(repository)},
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
        {
            "conn": conn,
            "conn_id": conn_id,
            "dialect": conn.engine,
            "profiles": _profiles_as_json(repository),
        },
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
        {
            "conn": conn,
            "conn_id": conn_id,
            "dialect": conn.engine,
            "profiles": _profiles_as_json(repository),
        },
    )


@router.get("/c/{conn_id}/browse/{table}", response_class=HTMLResponse)
def browse_page(
    conn_id: int, table: str, request: Request, repository: RepositoryDep
) -> HTMLResponse:
    """GET /c/{conn_id}/browse/{table} → browse workspace for that table."""
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
        "browse.html",
        {
            "conn": conn,
            "conn_id": conn_id,
            "table": table,
            "conn_name": conn.name,
            "profiles": _profiles_as_json(repository),
        },
    )
