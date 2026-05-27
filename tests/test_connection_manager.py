"""Skeleton tests for ConnectionManager — requires real adapters (phase-1)."""

import pytest

from pydbplay.core.connection_manager import ConnectionManager


def test_connection_manager_import() -> None:
    """ConnectionManager must be importable."""
    assert ConnectionManager is not None


def test_connection_manager_instantiates() -> None:
    """ConnectionManager can be instantiated without arguments."""
    cm = ConnectionManager()
    assert cm is not None


@pytest.mark.skip(reason="TODO(phase-1): requires SQLite adapter implementation")
def test_get_adapter_returns_adapter() -> None:
    """ConnectionManager.get_adapter() returns a DBAdapter for a valid profile."""
    ...
