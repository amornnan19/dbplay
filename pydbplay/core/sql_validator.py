"""SQL validation using sqlglot — FULLY IMPLEMENTED (not a stub).

Per SPEC §9 SQL validation flow.
"""

import re
from dataclasses import dataclass, field

import sqlglot
import sqlglot.errors
import sqlglot.expressions as exp

# Statement keys that are considered destructive (require confirm dialog).
# "truncatetable" is the sqlglot key for TRUNCATE TABLE (not "truncate").
_DESTRUCTIVE_KEYS: frozenset[str] = frozenset({"delete", "update", "drop", "truncatetable"})

# ---------------------------------------------------------------------------
# Shared read-only classifier (used by SQLiteAdapter and PostgresAdapter)
# ---------------------------------------------------------------------------

# Matches EXPLAIN [QUERY PLAN] prefix (SQLite / generic)
# Also matches Postgres parenthesized-options form: EXPLAIN (ANALYZE, ...) or EXPLAIN (FORMAT JSON)
_EXPLAIN_RE = re.compile(
    r"^\s*EXPLAIN\s+(?:QUERY\s+PLAN\s+)?(?:\([^)]*\)\s*)?",
    re.IGNORECASE,
)
# Matches a SELECT statement (plain or CTE: WITH ... SELECT)
_SELECT_RE = re.compile(
    r"^\s*(?:WITH\b.*?\bSELECT\b|SELECT\b)",
    re.IGNORECASE | re.DOTALL,
)
# Matches SHOW statement
_SHOW_RE = re.compile(r"^\s*SHOW\b", re.IGNORECASE)
# Matches: PRAGMA [schema.]name  or  PRAGMA [schema.]name(args)
# Does NOT match assignment form (PRAGMA name = value).
_PRAGMA_READ_RE = re.compile(
    r"^\s*PRAGMA\s+(?:[A-Za-z_][A-Za-z0-9_]*\s*\.\s*)?([A-Za-z_][A-Za-z0-9_]*)"
    r"\s*(?:\([^)]*\))?\s*;?\s*$",
    re.IGNORECASE,
)
# Matches the assignment form: PRAGMA [schema.]name = ...  or  PRAGMA [schema.]name=...
_PRAGMA_WRITE_RE = re.compile(
    r"^\s*PRAGMA\s+(?:[A-Za-z_][A-Za-z0-9_]*\s*\.\s*)?[A-Za-z_][A-Za-z0-9_]*\s*=",
    re.IGNORECASE,
)


def _tree_confirms_read_only(sql: str, dialect: str) -> bool:
    """Return True only if sqlglot's parse tree contains NO write operations.

    Checks two bypass patterns that look like SELECTs but write data:
      1. ``SELECT … INTO <table>`` — the top-level Select has ``args["into"]`` set.
      2. Data-modifying CTEs: any ``Insert``, ``Update``, or ``Delete`` node anywhere
         in the tree (catches ``WITH x AS (INSERT/UPDATE/DELETE …) SELECT …``).

    Fail-closed: if sqlglot cannot parse the statement, returns False (reject).
    This is intentional — an unparseable statement must not pass through via the
    regex fast-path in read-only mode.

    Args:
        sql: The SQL string that already passed the SELECT regex.
        dialect: sqlglot dialect name (e.g. ``"postgres"``, ``"sqlite"``).

    Returns:
        True when the tree contains only safe read operations; False otherwise.
    """
    effective_dialect: str | None = dialect if dialect else None
    try:
        parsed = sqlglot.parse_one(
            sql,
            dialect=effective_dialect,
            error_level=sqlglot.errors.ErrorLevel.RAISE,
        )
    except (sqlglot.errors.ParseError, Exception):
        # Fail closed: unparseable SQL is not allowed through in read-only mode.
        return False

    if parsed is None:
        return False

    # Pattern 1: SELECT … INTO <table>  (creates a new table from SELECT result)
    if isinstance(parsed, exp.Select) and parsed.args.get("into") is not None:
        return False

    # Pattern 2: any INSERT / UPDATE / DELETE node anywhere in the tree
    # (covers data-modifying CTEs and any other nested DML)
    if parsed.find(exp.Insert, exp.Update, exp.Delete) is not None:
        return False

    return True


def is_read_only_statement(
    sql: str,
    dialect: str,
    *,
    extra_read_pragmas: frozenset[str] = frozenset(),
    allow_show: bool = False,
) -> bool:
    """Return True only for statements that are safe to execute in read-only mode.

    A statement is read-only iff:
    - It is a SELECT (including a leading CTE ``WITH … SELECT``), OR
    - It is ``EXPLAIN`` / ``EXPLAIN QUERY PLAN`` wrapping a read (strip the prefix
      and re-check the inner statement), OR
    - ``allow_show=True`` and it is a ``SHOW`` statement, OR
    - It is a **read PRAGMA**: matches ``PRAGMA <name>`` or ``PRAGMA <name>(args)``
      with NO ``=`` assignment, AND ``<name>`` is in ``extra_read_pragmas``.

    Args:
        sql: The SQL statement to classify.
        dialect: Engine dialect (e.g. ``"sqlite"``, ``"postgres"``). Currently used
                 only for documentation; routing of dialect-specific rules is done
                 via *extra_read_pragmas* and *allow_show*.
        extra_read_pragmas: Set of PRAGMA names that are considered read-only
                            (SQLite-specific; ignored for non-PRAGMA statements).
        allow_show: When True, ``SHOW …`` statements are allowed (Postgres).

    Returns:
        True if the statement is safe for read-only mode, False otherwise.
    """
    stripped = sql.strip()

    # Handle EXPLAIN / EXPLAIN QUERY PLAN — strip prefix and re-check inner stmt
    explain_match = _EXPLAIN_RE.match(stripped)
    if explain_match:
        inner = stripped[explain_match.end() :]
        return is_read_only_statement(
            inner,
            dialect,
            extra_read_pragmas=extra_read_pragmas,
            allow_show=allow_show,
        )

    # SELECT (plain or CTE) — regex fast-path, followed by a tree-based DML scan
    # to catch write-inside-read bypasses such as:
    #   SELECT 1 INTO leaked                        (SELECT … INTO …)
    #   WITH x AS (INSERT … RETURNING *) SELECT …  (data-modifying CTE)
    #   WITH x AS (UPDATE … RETURNING *) SELECT …
    #   WITH x AS (DELETE … RETURNING *) SELECT …
    if _SELECT_RE.match(stripped):
        return _tree_confirms_read_only(stripped, dialect)

    # SHOW — optional, dialect-specific (Postgres)
    if allow_show and _SHOW_RE.match(stripped):
        return True

    # PRAGMA — reject assignment forms immediately
    if _PRAGMA_WRITE_RE.match(stripped):
        return False

    pragma_match = _PRAGMA_READ_RE.match(stripped)
    if pragma_match and extra_read_pragmas:
        pragma_name = pragma_match.group(1).lower()
        return pragma_name in extra_read_pragmas

    return False


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
