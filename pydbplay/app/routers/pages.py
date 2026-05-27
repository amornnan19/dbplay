"""HTML page routes rendered via Jinja2."""

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

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
