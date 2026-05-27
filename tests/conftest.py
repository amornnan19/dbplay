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
    """A TestClient bound to the FastAPI app (sync, no asyncio needed)."""
    with TestClient(app) as c:
        yield c
