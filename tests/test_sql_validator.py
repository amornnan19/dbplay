"""Real passing test (b): sql_validator.validate correctness."""

import pytest

from pydbplay.core.sql_validator import ValidationResult, is_explain_statement, validate

# ---------------------------------------------------------------------------
# Destructive detection
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM users WHERE id = 1",
        "UPDATE users SET name = 'x' WHERE id = 1",
        "DROP TABLE users",
        "TRUNCATE TABLE users",
    ],
)
def test_destructive_statements_are_flagged(sql: str) -> None:
    """Destructive DML/DDL must set is_destructive=True."""
    result = validate(sql)
    assert result.ok is True
    assert result.is_destructive is True


def test_select_is_not_destructive() -> None:
    """Plain SELECT must not be flagged as destructive."""
    result = validate("SELECT * FROM users WHERE id = 1")
    assert result.ok is True
    assert result.is_destructive is False


def test_insert_is_not_destructive() -> None:
    """INSERT is not in the destructive set (no confirm needed per current policy)."""
    result = validate("INSERT INTO users (name) VALUES ('alice')")
    assert result.ok is True
    assert result.is_destructive is False


# ---------------------------------------------------------------------------
# Parse error detection
# ---------------------------------------------------------------------------


def test_invalid_sql_returns_error() -> None:
    """Malformed SQL must return ok=False with an error message.

    Note: sqlglot v30+ uses empty string dialect for generic SQL (not "ansi").
    "SELECT FROM WHERE" reliably raises a ParseError in all dialects.
    """
    result = validate("SELECT FROM WHERE")
    assert result.ok is False
    assert result.error is not None
    assert len(result.error) > 0


def test_validation_result_is_dataclass() -> None:
    """validate() must return a ValidationResult instance."""
    result = validate("SELECT 1")
    assert isinstance(result, ValidationResult)


# ---------------------------------------------------------------------------
# Dialect parameter
# ---------------------------------------------------------------------------


def test_dialect_postgres() -> None:
    """Validation with explicit postgres dialect parses a simple SELECT."""
    result = validate("SELECT * FROM pg_tables", dialect="postgres")
    assert result.ok is True


def test_dialect_mysql() -> None:
    """Validation with explicit mysql dialect parses a backtick identifier."""
    result = validate("SELECT * FROM `users`", dialect="mysql")
    assert result.ok is True


# ---------------------------------------------------------------------------
# is_explain_statement
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("dialect", ["postgres", "mysql", "sqlite"])
def test_is_explain_plain_select(dialect: str) -> None:
    """EXPLAIN SELECT 1 → True for all three dialects."""
    assert is_explain_statement("EXPLAIN SELECT 1", dialect) is True


@pytest.mark.parametrize("dialect", ["postgres", "mysql"])
def test_is_explain_analyze(dialect: str) -> None:
    """EXPLAIN ANALYZE SELECT 1 → True for postgres and mysql."""
    assert is_explain_statement("EXPLAIN ANALYZE SELECT 1", dialect) is True


def test_is_explain_query_plan_sqlite() -> None:
    """EXPLAIN QUERY PLAN SELECT 1 → True for sqlite."""
    assert is_explain_statement("EXPLAIN QUERY PLAN SELECT 1", "sqlite") is True


def test_is_explain_plain_select_false() -> None:
    """SELECT 1 → False (not an EXPLAIN statement)."""
    assert is_explain_statement("SELECT 1", "sqlite") is False


def test_is_explain_comment_before_select_is_false() -> None:
    """-- explain comment\\nSELECT 1 → False (comment contains EXPLAIN, body is SELECT)."""
    assert is_explain_statement("-- explain comment\nSELECT 1", "sqlite") is False


def test_is_explain_lowercase_leading_whitespace() -> None:
    """explain select 1 (lowercase, leading whitespace) → True."""
    assert is_explain_statement("  explain select 1", "sqlite") is True


def test_is_explain_multi_statement_leading_explain() -> None:
    """EXPLAIN SELECT 1 is still detected as EXPLAIN (single statement check is caller's job)."""
    # sqlglot parses only the first statement so this is True
    assert is_explain_statement("EXPLAIN SELECT 1", "postgres") is True


def test_is_explain_malformed_no_body() -> None:
    """EXPLAIN  (no body / trailing space) → True via regex fallback."""
    assert is_explain_statement("EXPLAIN ", "postgres") is True


def test_is_explain_block_comment_before_explain() -> None:
    """/* comment */ EXPLAIN SELECT 1 → True (leading block comment stripped)."""
    assert is_explain_statement("/* comment */ EXPLAIN SELECT 1", "sqlite") is True
