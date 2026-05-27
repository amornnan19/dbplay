"""SQL validation using sqlglot — FULLY IMPLEMENTED (not a stub).

Per SPEC §9 SQL validation flow.
"""

from dataclasses import dataclass, field

import sqlglot
import sqlglot.errors

# Statement keys that are considered destructive (require confirm dialog).
# "truncatetable" is the sqlglot key for TRUNCATE TABLE (not "truncate").
_DESTRUCTIVE_KEYS: frozenset[str] = frozenset({"delete", "update", "drop", "truncatetable"})


@dataclass
class ValidationResult:
    """Result of a SQL validation check.

    Attributes:
        ok: True when sqlglot parsed without error.
        is_destructive: True when any statement is DELETE/UPDATE/DROP/TRUNCATE.
        error: Human-readable parse error message (only when ok=False).
        line: Source line of the first parse error (only when ok=False).
        col: Source column of the first parse error (only when ok=False).
    """

    ok: bool
    is_destructive: bool = False
    error: str | None = None
    line: int | None = None
    col: int | None = None
    # The parsed statement types for introspection / testing
    statement_types: list[str] = field(default_factory=list)


def validate(sql: str, dialect: str = "") -> ValidationResult:
    """Parse *sql* and return a ValidationResult.

    Args:
        sql: The SQL string to validate.
        dialect: sqlglot dialect name — ``"postgres"``, ``"mysql"``,
                 ``"sqlite"``, or ``""`` (empty string = generic SQL, default).

    Returns:
        ValidationResult with ``ok``, ``is_destructive``, and on parse error
        also ``error``, ``line``, ``col``.

    Example::

        result = validate("DELETE FROM users WHERE id = 1", dialect="postgres")
        assert result.ok is True
        assert result.is_destructive is True

        result = validate("SELEKT * FROM t", dialect="postgres")
        assert result.ok is False
        assert result.error is not None
    """
    # sqlglot v30+: empty string = generic SQL (no specific dialect)
    # "ansi" is NOT a valid dialect name in sqlglot v30
    effective_dialect: str | None = dialect if dialect else None
    try:
        parsed = sqlglot.parse(
            sql,
            dialect=effective_dialect,
            error_level=sqlglot.errors.ErrorLevel.RAISE,
        )
    except sqlglot.errors.ParseError as exc:
        # Extract line/col from the first error if available
        line: int | None = None
        col: int | None = None
        if exc.errors:
            first = exc.errors[0]
            line = first.get("line")
            col = first.get("col")
        return ValidationResult(
            ok=False,
            error=str(exc),
            line=line,
            col=col,
        )

    statement_types = [stmt.key for stmt in parsed if stmt is not None]
    is_destructive = any(key in _DESTRUCTIVE_KEYS for key in statement_types)

    return ValidationResult(
        ok=True,
        is_destructive=is_destructive,
        statement_types=statement_types,
    )
