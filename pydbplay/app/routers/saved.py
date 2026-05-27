"""API routes for saved queries (SPEC §8 Phase 5)."""

# Endpoints:
#   POST   /api/c/{conn_id}/saved-queries              → saved_queries partial (HTML)
#   GET    /api/c/{conn_id}/saved-queries              → saved_queries partial (HTML)
#   DELETE /api/c/{conn_id}/saved-queries/{saved_id}  → 204 No Content

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Form
from fastapi.responses import HTMLResponse, Response
from fastapi.templating import Jinja2Templates
from starlette.requests import Request

from pydbplay.app.dependencies import RepositoryDep
from pydbplay.app.exceptions import ConnectionNotFound

_TEMPLATES_DIR = Path(__file__).parent.parent / "templates"
_templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

router = APIRouter(prefix="/api/c", tags=["saved"])


def _saved_list_response(
    request: Request,
    repository: RepositoryDep,
    conn_id: int,
    *,
    trigger: bool = False,
) -> HTMLResponse:
    """Render the saved_queries partial and optionally set HX-Trigger."""
    saved = repository.list_saved_queries(conn_id)
    response = _templates.TemplateResponse(
        request,
        "partials/saved_queries.html",
        {"saved": saved, "conn_id": conn_id},
    )
    if trigger:
        response.headers["HX-Trigger"] = "saved-changed"
    return response


@router.post("/{conn_id}/saved-queries", response_class=HTMLResponse)
def create_saved_query(
    conn_id: int,
    request: Request,
    repository: RepositoryDep,
    name: Annotated[str, Form(min_length=1, max_length=200)],
    sql: Annotated[str, Form(min_length=1)],
    description: Annotated[str | None, Form()] = None,
) -> HTMLResponse:
    """POST /api/c/{conn_id}/saved-queries → saved_queries partial."""
    if repository.get_connection(conn_id) is None:
        raise ConnectionNotFound(conn_id)
    repository.create_saved_query(
        name=name,
        sql=sql,
        connection_id=conn_id,
        description=description or None,
    )
    return _saved_list_response(request, repository, conn_id, trigger=True)


@router.get("/{conn_id}/saved-queries", response_class=HTMLResponse)
def list_saved_queries(
    conn_id: int,
    request: Request,
    repository: RepositoryDep,
) -> HTMLResponse:
    """GET /api/c/{conn_id}/saved-queries → saved_queries partial."""
    if repository.get_connection(conn_id) is None:
        raise ConnectionNotFound(conn_id)
    return _saved_list_response(request, repository, conn_id)


@router.delete("/{conn_id}/saved-queries/{saved_id}")
def delete_saved_query(
    conn_id: int,
    saved_id: int,
    request: Request,
    repository: RepositoryDep,
) -> Response:
    """DELETE /api/c/{conn_id}/saved-queries/{saved_id} → 204 No Content."""
    if repository.get_connection(conn_id) is None:
        raise ConnectionNotFound(conn_id)
    repository.delete_saved_query(saved_id)
    response = Response(status_code=204)
    response.headers["HX-Trigger"] = "saved-changed"
    return response
