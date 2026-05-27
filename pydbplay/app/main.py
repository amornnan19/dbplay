"""FastAPI application factory."""

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from pydbplay.app.exceptions import register_exception_handlers
from pydbplay.app.routers import connections, export, pages, query, rows, schema

_APP_DIR = Path(__file__).parent
_TEMPLATES_DIR = _APP_DIR / "templates"
_STATIC_DIR = _APP_DIR / "static"


def get_templates() -> Jinja2Templates:
    """Return the shared Jinja2Templates instance."""
    return Jinja2Templates(directory=str(_TEMPLATES_DIR))


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    app = FastAPI(
        title="pydbplay",
        description="Local-first DB query playground",
        version="0.1.0",
        # Disable docs on non-localhost in future; fine for dev
        docs_url="/docs",
        redoc_url="/redoc",
    )

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
