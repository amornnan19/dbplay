"""API routes for query execution and history (SPEC §6)."""

# Endpoints:
#   POST /api/c/{conn_id}/query               → result_grid partial (HTML)
#   POST /api/c/{conn_id}/query/validate      → validation partial (HTML)
#   GET  /api/c/{conn_id}/query/history       → query_history partial (HTML)
#   GET  /api/c/{conn_id}/query/autocomplete  → TODO(phase-autocomplete)

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Form
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from starlette.requests import Request

from pydbplay.adapters.base import AdapterError, ReadOnlyViolationError, UnsupportedEngineError
from pydbplay.app.dependencies import QueryExecutorDep, RepositoryDep
from pydbplay.app.exceptions import ConnectionNotFound
from pydbplay.core.query_executor import MultipleStatementsError, QueryError
from pydbplay.core.sql_validator import validate as sql_validate

_TEMPLATES_DIR = Path(__file__).parent.parent / "templates"
_templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

router = APIRouter(prefix="/api/c", tags=["query"])


@router.post("/{conn_id}/query", response_class=HTMLResponse)
def run_query(
    conn_id: int,
    request: Request,
    repository: RepositoryDep,
    query_executor: QueryExecutorDep,
    sql: Annotated[str, Form()],
    limit: Annotated[int, Form(ge=1, le=100_000)] = 1000,
    enforce_limit: Annotated[bool, Form()] = True,
) -> HTMLResponse:
    """POST /api/c/{conn_id}/query → result_grid partial."""
    if repository.get_connection(conn_id) is None:
        raise ConnectionNotFound(conn_id)
    try:
        result = query_executor.run(conn_id, sql, limit=limit, enforce_limit=enforce_limit)
    except UnsupportedEngineError:
        raise
    except (QueryError, MultipleStatementsError, AdapterError, ReadOnlyViolationError) as exc:
        return _templates.TemplateResponse(
            request,
            "partials/result_grid.html",
            {"error": str(exc), "result": None},
        )
    return _templates.TemplateResponse(
        request,
        "partials/result_grid.html",
        {"result": result, "error": None},
    )


@router.post("/{conn_id}/query/validate", response_class=HTMLResponse)
def validate_query(
    conn_id: int,
    request: Request,
    repository: RepositoryDep,
    sql: Annotated[str, Form()],
) -> HTMLResponse:
    """POST /api/c/{conn_id}/query/validate → validation partial."""
    profile = repository.get_connection(conn_id)
    if profile is None:
        raise ConnectionNotFound(conn_id)
    # Resolve dialect from stored profile — avoids opening a live DB connection.
    # profile.engine values ("sqlite", "postgres", "mysql") are valid sqlglot
    # dialect names directly.
    dialect: str = profile.engine
    validation = sql_validate(sql, dialect=dialect)
    return _templates.TemplateResponse(
        request,
        "partials/validation.html",
        {"validation": validation, "dialect": dialect},
    )


@router.get("/{conn_id}/query/history", response_class=HTMLResponse)
def query_history(
    conn_id: int,
    request: Request,
    query_executor: QueryExecutorDep,
) -> HTMLResponse:
    """GET /api/c/{conn_id}/query/history → query_history partial."""
    history = query_executor.history(conn_id, limit=50)
    return _templates.TemplateResponse(
        request,
        "partials/query_history.html",
        {"history": history},
    )


# TODO(phase-autocomplete): GET /{conn_id}/query/autocomplete
# Needs the schema router to resolve table/column names.
