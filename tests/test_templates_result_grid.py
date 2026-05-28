"""Unit tests for partials/result_grid.html — rendered directly via Jinja2.

These tests cover rendering paths that are not exercised by the router-level
integration tests (e.g. the single-column EXPLAIN <pre> branch).
"""

from pathlib import Path

import jinja2
import pytest

from pydbplay.schemas.query import QueryRunResult

# ---------------------------------------------------------------------------
# Jinja2 environment pointed at the app templates directory
# ---------------------------------------------------------------------------

_TEMPLATES_DIR = Path(__file__).parent.parent / "pydbplay" / "app" / "templates"


@pytest.fixture()
def jinja_env() -> jinja2.Environment:
    loader = jinja2.FileSystemLoader(str(_TEMPLATES_DIR))
    return jinja2.Environment(loader=loader, autoescape=True)


def _render_result_grid(
    jinja_env: jinja2.Environment,
    result: QueryRunResult,
    *,
    conn_id: str = "test-conn",
    executed_sql: str = "EXPLAIN SELECT 1",
    error: str | None = None,
) -> str:
    tmpl = jinja_env.get_template("partials/result_grid.html")
    return tmpl.render(
        result=result,
        conn_id=conn_id,
        executed_sql=executed_sql,
        error=error,
    )


# ---------------------------------------------------------------------------
# Single-column EXPLAIN branch (Postgres "QUERY PLAN" shape)
# ---------------------------------------------------------------------------


def test_single_column_explain_renders_pre_block(jinja_env: jinja2.Environment) -> None:
    """Single-column EXPLAIN result must be rendered inside a <pre> element."""
    result = QueryRunResult(
        columns=["QUERY PLAN"],
        rows=[["Seq Scan on customers"], ["  Filter: city = 'Bangkok'"]],
        row_count=2,
        duration_ms=3,
        effective_sql="EXPLAIN SELECT * FROM customers WHERE city = 'Bangkok'",
        is_explain=True,
    )
    rendered = _render_result_grid(jinja_env, result)

    assert "<pre" in rendered, "Expected a <pre> element for single-column EXPLAIN"
    assert "Seq Scan on customers" in rendered
    assert "  Filter: city = &#39;Bangkok&#39;" in rendered or "Filter: city" in rendered
    # Both plan lines must appear (newline-separated inside the <pre>)
    assert "Seq Scan on customers" in rendered
    assert "Filter: city" in rendered
    # EXPLAIN badge sentinel
    assert "EXPLAIN" in rendered


def test_single_column_explain_plan_lines_separated_by_newline(
    jinja_env: jinja2.Environment,
) -> None:
    """The two plan lines must be separated by a newline inside the <pre> block."""
    result = QueryRunResult(
        columns=["QUERY PLAN"],
        rows=[["Seq Scan on customers"], ["  Filter: city = 'Bangkok'"]],
        row_count=2,
        duration_ms=3,
        effective_sql="EXPLAIN SELECT * FROM customers WHERE city = 'Bangkok'",
        is_explain=True,
    )
    rendered = _render_result_grid(jinja_env, result)

    # Extract the <pre> block content and verify newline separation
    pre_start = rendered.find("<pre")
    pre_end = rendered.find("</pre>", pre_start)
    assert pre_start != -1 and pre_end != -1, "No <pre>...</pre> block found"
    pre_content = rendered[pre_start:pre_end]

    assert "Seq Scan on customers" in pre_content
    # After the first plan line there must be a newline before the next line
    idx_first = pre_content.find("Seq Scan on customers")
    assert "\n" in pre_content[idx_first:], "Expected newline after first plan line"


def test_single_column_explain_export_toolbar_present(jinja_env: jinja2.Environment) -> None:
    """Export toolbar (CSV/JSON/SQL buttons) must appear for non-empty EXPLAIN results."""
    result = QueryRunResult(
        columns=["QUERY PLAN"],
        rows=[["Seq Scan on customers"]],
        row_count=1,
        duration_ms=1,
        effective_sql="EXPLAIN SELECT * FROM customers",
        is_explain=True,
    )
    rendered = _render_result_grid(
        jinja_env, result, executed_sql="EXPLAIN SELECT * FROM customers"
    )

    assert "CSV" in rendered
    assert "JSON" in rendered
    assert "SQL" in rendered


def test_single_column_explain_no_export_toolbar_when_empty(
    jinja_env: jinja2.Environment,
) -> None:
    """Export toolbar must NOT appear when row_count is 0."""
    result = QueryRunResult(
        columns=["QUERY PLAN"],
        rows=[],
        row_count=0,
        duration_ms=0,
        effective_sql="EXPLAIN SELECT * FROM customers",
        is_explain=True,
    )
    rendered = _render_result_grid(
        jinja_env, result, executed_sql="EXPLAIN SELECT * FROM customers"
    )

    # The Export label should not appear
    assert "Export:" not in rendered
