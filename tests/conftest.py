"""Shared pytest fixtures."""

import pytest
from fastapi.testclient import TestClient

from pydbplay.app.main import create_app


@pytest.fixture(scope="session")
def app(tmp_path_factory: pytest.TempPathFactory):
    """Create a fresh FastAPI app instance backed by a throwaway temp directory."""
    tmp = tmp_path_factory.mktemp("pydbplay")
    import os

    os.environ["PYDBPLAY_APP_DIR"] = str(tmp)
    application = create_app()
    yield application
    # Clean up env var after session so it doesn't bleed into other processes
    os.environ.pop("PYDBPLAY_APP_DIR", None)


@pytest.fixture(scope="session")
def client(app):
    """A TestClient bound to the FastAPI app (sync, no asyncio needed).

    base_url="http://localhost" — TestClient defaults to "http://testserver" which
    produces a Host header of "testserver".  That host is not in the security
    middleware's allowed_hosts list (["127.0.0.1", "localhost"]), so every request
    would return 400.  Setting base_url to "http://localhost" makes TestClient send
    Host: localhost, which passes the allowlist.

    Auto-CSRF mechanism: a priming GET populates the csrf_token cookie via the
    middleware's set-cookie-if-missing logic.  An httpx event_hook then copies that
    cookie value into the X-CSRFToken request header for every unsafe method (POST,
    PUT, PATCH, DELETE), satisfying the CSRF double-submit check automatically so
    that existing mutating connection tests need no changes.
    """

    def _inject_csrf(request):  # type: ignore[no-untyped-def]
        """httpx request hook: copy csrf_token cookie → X-CSRFToken header."""
        unsafe = {"POST", "PUT", "PATCH", "DELETE"}
        if request.method.upper() in unsafe:
            # The cookie jar is attached to the transport; read it from the client.
            token = c.cookies.get("csrf_token")
            if token:
                request.headers["X-CSRFToken"] = token

    with TestClient(app, base_url="http://localhost") as c:
        # Attach the hook BEFORE the priming GET so the hook is active for all
        # subsequent requests.
        c.event_hooks["request"] = [_inject_csrf]
        # Prime: perform a GET so the middleware sets the csrf_token cookie.
        c.get("/health")
        yield c
