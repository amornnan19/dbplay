"""Real passing test (b): sql_validator.validate correctness."""

import pytest

from pydbplay.core.sql_validator import ValidationResult, validate

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
