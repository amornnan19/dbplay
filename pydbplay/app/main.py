"""FastAPI application factory."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from pydbplay.app.exceptions import register_exception_handlers
from pydbplay.app.middleware import LocalhostSecurityMiddleware
from pydbplay.app.routers import connections, export, pages, query, rows, schema
from pydbplay.config import get_settings
from pydbplay.core.connection_manager import ConnectionManager
from pydbplay.core.exporter import Exporter
from pydbplay.core.query_executor import QueryExecutor
from pydbplay.core.row_editor import RowEditor
from pydbplay.db.repository import Repository, make_engine, run_migrations

_APP_DIR = Path(__file__).parent
_TEMPLATES_DIR = _APP_DIR / "templates"
_STATIC_DIR = _APP_DIR / "static"


def get_templates() -> Jinja2Templates:
    """Return the shared Jinja2Templates instance."""
    return Jinja2Templates(directory=str(_TEMPLATES_DIR))


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    settings = get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # ── Startup ──────────────────────────────────────────────────────
        settings.app_dir.mkdir(parents=True, exist_ok=True)
        engine = make_engine(settings.db_file)
        with engine.connect() as conn:
            run_migrations(conn)
        repository = Repository(engine)
        connection_manager = ConnectionManager(repository)
        query_executor = QueryExecutor(connection_manager, repository)
        row_editor = RowEditor(connection_manager)
        exporter = Exporter(connection_manager)
        app.state.repository = repository
        app.state.connection_manager = connection_manager
        app.state.query_executor = query_executor
        app.state.row_editor = row_editor
        app.state.exporter = exporter
        app.state.engine = engine

        yield

        # ── Shutdown ──────────────────────────────────────────────────────
        connection_manager.close_all()
        engine.dispose()

    app = FastAPI(
        title="pydbplay",
        description="Local-first DB query playground",
        version="0.1.0",
        # Disable docs on non-localhost in future; fine for dev
        docs_url="/docs",
        redoc_url="/redoc",
        lifespan=lifespan,
    )

    # Security middleware — must be added before static files / routers
    app.add_middleware(LocalhostSecurityMiddleware, settings=settings)  # type: ignore[arg-type]

    # Static files
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

    # Exception handlers
    register_exception_handlers(app)

    # Routers
    app.include_router(pages.router)
    app.include_router(connections.router)
    app.include_router(schema.router)
    app.include_router(query.router)
    app.include_router(rows.router)
    app.include_router(export.router)

    @app.get("/health", include_in_schema=True, tags=["health"])
    def health_check() -> JSONResponse:
        """Health probe — returns 200 when the server is up."""
        return JSONResponse({"status": "ok"})

    return app


# Module-level app instance — used by uvicorn and TestClient
app = create_app()
