"""Shared pytest fixtures."""

import pytest
from fastapi.testclient import TestClient

from pydbplay.app.main import create_app


@pytest.fixture(scope="session")
def app():
    """Create a fresh FastAPI app instance for the test session."""
    return create_app()


@pytest.fixture(scope="session")
def client(app):
    """A TestClient bound to the FastAPI app (sync, no asyncio needed)."""
    with TestClient(app) as c:
        yield c
