"""API routes for connection profile CRUD — HTML partials (HTMX)."""

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Form, Response
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from starlette.requests import Request

from pydbplay.adapters.base import AdapterError, UnsupportedEngineError
from pydbplay.app.dependencies import ConnectionManagerDep, RepositoryDep
from pydbplay.schemas.connection import ConnectionCreate, ConnectionTestResult, ConnectionUpdate

_TEMPLATES_DIR = Path(__file__).parent.parent / "templates"
_templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

router = APIRouter(prefix="/api/connections", tags=["connections"])


# ---------------------------------------------------------------------------
# Form → schema helpers
# ---------------------------------------------------------------------------


def _create_from_form(
    name: str,
    engine: str,
    database: str,
    host: str | None,
    port: str | None,
    username: str | None,
    password: str | None,
    ssl_mode: str | None,
    read_only: str | None,
    color: str | None,
) -> ConnectionCreate:
    """Assemble a ConnectionCreate from raw form strings."""
    return ConnectionCreate(
        name=name,
        engine=engine,  # type: ignore[arg-type]
        database=database,
        host=host or None,
        port=int(port) if port else None,
        username=username or None,
        password=password or None,
        ssl_mode=ssl_mode or None,
        read_only=read_only in ("on", "true", "1", "yes"),
        color=color or None,
    )


def _update_from_form(
    name: str | None,
    database: str | None,
    host: str | None,
    port: str | None,
    username: str | None,
    password: str | None,
    ssl_mode: str | None,
    read_only: str | None,
    color: str | None,
) -> ConnectionUpdate:
    """Assemble a ConnectionUpdate from raw form strings."""
    updates: dict[str, object] = {}
    if name is not None:
        updates["name"] = name
    if database is not None:
        updates["database"] = database
    if host is not None:
        updates["host"] = host or None
    if port is not None:
        updates["port"] = int(port) if port else None
    if username is not None:
        updates["username"] = username or None
    if password is not None:
        updates["password"] = password or None
    if ssl_mode is not None:
        updates["ssl_mode"] = ssl_mode or None
    if read_only is not None:
        updates["read_only"] = read_only in ("on", "true", "1", "yes")
    if color is not None:
        updates["color"] = color or None
    return ConnectionUpdate.model_validate(updates)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("", response_class=HTMLResponse)
def list_connections(request: Request, repository: RepositoryDep) -> HTMLResponse:
    """GET /api/connections → connection_list partial."""
    connections = repository.list_connections()
    return _templates.TemplateResponse(
        request,
        "partials/connection_list.html",
        {"connections": connections},
    )


@router.post("", response_class=HTMLResponse)
def create_connection(
    request: Request,
    repository: RepositoryDep,
    name: Annotated[str, Form()],
    engine: Annotated[str, Form()],
    database: Annotated[str, Form()],
    host: Annotated[str | None, Form()] = None,
    port: Annotated[str | None, Form()] = None,
    username: Annotated[str | None, Form()] = None,
    password: Annotated[str | None, Form()] = None,
    ssl_mode: Annotated[str | None, Form()] = None,
    read_only: Annotated[str | None, Form()] = None,
    color: Annotated[str | None, Form()] = None,
) -> HTMLResponse:
    """POST /api/connections → create and return refreshed list partial."""
    data = _create_from_form(
        name, engine, database, host, port, username, password, ssl_mode, read_only, color
    )
    # TODO(phase-crypto): encrypt data.password before storing
    repository.create_connection(data)
    connections = repository.list_connections()
    return _templates.TemplateResponse(
        request,
        "partials/connection_list.html",
        {"connections": connections},
    )


@router.post("/test", response_class=HTMLResponse)
def test_connection(
    request: Request,
    connection_manager: ConnectionManagerDep,
    name: Annotated[str, Form()],
    engine: Annotated[str, Form()],
    database: Annotated[str, Form()],
    host: Annotated[str | None, Form()] = None,
    port: Annotated[str | None, Form()] = None,
    username: Annotated[str | None, Form()] = None,
    password: Annotated[str | None, Form()] = None,
    ssl_mode: Annotated[str | None, Form()] = None,
    read_only: Annotated[str | None, Form()] = None,
    color: Annotated[str | None, Form()] = None,
) -> HTMLResponse:
    """POST /api/connections/test → connection_test_result partial."""
    data = _create_from_form(
        name, engine, database, host, port, username, password, ssl_mode, read_only, color
    )
    result: ConnectionTestResult = connection_manager.test_connection(data)
    return _templates.TemplateResponse(
        request,
        "partials/connection_test_result.html",
        {"result": result},
    )


@router.patch("/{conn_id}", response_class=HTMLResponse)
def update_connection(
    conn_id: int,
    request: Request,
    repository: RepositoryDep,
    name: Annotated[str | None, Form()] = None,
    database: Annotated[str | None, Form()] = None,
    host: Annotated[str | None, Form()] = None,
    port: Annotated[str | None, Form()] = None,
    username: Annotated[str | None, Form()] = None,
    password: Annotated[str | None, Form()] = None,
    ssl_mode: Annotated[str | None, Form()] = None,
    read_only: Annotated[str | None, Form()] = None,
    color: Annotated[str | None, Form()] = None,
) -> HTMLResponse:
    """PATCH /api/connections/{conn_id} → refreshed list partial (or 404 partial)."""
    data = _update_from_form(
        name, database, host, port, username, password, ssl_mode, read_only, color
    )
    profile = repository.update_connection(conn_id, data)
    if profile is None:
        return _templates.TemplateResponse(
            request,
            "partials/connection_error.html",
            {"message": f"Connection {conn_id} not found."},
            status_code=404,
        )
    connections = repository.list_connections()
    return _templates.TemplateResponse(
        request,
        "partials/connection_list.html",
        {"connections": connections},
    )


@router.delete("/{conn_id}")
def delete_connection(
    conn_id: int,
    repository: RepositoryDep,
    connection_manager: ConnectionManagerDep,
) -> Response:
    """DELETE /api/connections/{conn_id} → 204 No Content."""
    connection_manager.disconnect(conn_id)
    repository.delete_connection(conn_id)
    return Response(status_code=204)


@router.post("/{conn_id}/connect", response_class=HTMLResponse)
def connect(
    conn_id: int,
    request: Request,
    repository: RepositoryDep,
    connection_manager: ConnectionManagerDep,
) -> HTMLResponse:
    """POST /api/connections/{conn_id}/connect → refreshed list partial.

    Builds (or retrieves from cache) the adapter for the given connection,
    which records last_used_at in the repository.

    TODO(phase-dashboard): return HX-Redirect to /c/{id} once the dashboard
    page exists.
    """
    try:
        connection_manager.get_adapter(conn_id)
    except KeyError:
        return _templates.TemplateResponse(
            request,
            "partials/connection_error.html",
            {"message": f"Connection {conn_id} not found."},
            status_code=404,
        )
    except UnsupportedEngineError as exc:
        return _templates.TemplateResponse(
            request,
            "partials/connection_error.html",
            {"message": str(exc)},
            status_code=422,
        )
    except AdapterError as exc:
        return _templates.TemplateResponse(
            request,
            "partials/connection_error.html",
            {"message": str(exc)},
            status_code=400,
        )

    connections = repository.list_connections()
    return _templates.TemplateResponse(
        request,
        "partials/connection_list.html",
        {"connections": connections},
    )
