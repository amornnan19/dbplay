"""Skeleton tests for QueryExecutor — most require a real adapter (phase-1)."""

import pytest

from pydbplay.core.query_executor import QueryExecutor


def test_query_executor_import() -> None:
    """QueryExecutor must be importable."""
    assert QueryExecutor is not None


@pytest.mark.skip(reason="TODO(phase-1): requires a live adapter")
def test_query_executor_run_select() -> None:
    """QueryExecutor.run() returns a QueryResult for a valid SELECT."""
    ...


@pytest.mark.skip(reason="TODO(phase-1): requires a live adapter")
def test_query_executor_auto_limit() -> None:
    """QueryExecutor auto-injects LIMIT 1000 when none is present."""
    ...
