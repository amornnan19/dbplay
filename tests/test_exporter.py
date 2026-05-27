"""Tests for Exporter — real SQLite target db, no Docker required.

Covers:
- CSV: round-trip via csv.reader; adversarial values (comma, double-quote,
  single-quote, newline, O'Brien); NULL → empty field; header present.
- JSON: json.loads round-trip; NULL → None; Decimal/datetime/bytes no-raise.
- SQL: INSERT format; O'Brien → 'O''Brien'; NULL → NULL; identifiers quoted;
  execution round-trip in a fresh SQLite copy (backslash/bytes/quote values).
- _sql_literal unit tests: per-dialect string/bytes/non-finite float escaping.
- Streaming: small chunk_size over >chunk_size rows yields complete output.
- export_table whitelisting: unknown table raises UnknownIdentifierError.
- Read-only connection: export (SELECT) still succeeds.
- Empty result: all three formats handle gracefully.
"""

import csv
import io
import json
import math
import sqlite3
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import pytest

from pydbplay.adapters.base import UnknownIdentifierError
from pydbplay.core.connection_manager import ConnectionManager
from pydbplay.core.exporter import Exporter, ExporterError, _sql_literal
from pydbplay.db.repository import Repository, make_engine, run_migrations
from pydbplay.schemas.connection import ConnectionCreate

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Adversarial seed rows:
# id | label (adversarial text)                | val   | extra | blob
# ---+------------------------------------------+-------+-------+----------
# 1  | normal                                   | 1.0   | NULL  | NULL
# 2  | has,comma                                | 2.5   | NULL  | NULL
# 3  | has"double"quote                         | 3.0   | NULL  | NULL
# 4  | O'Brien                                  | 4.0   | NULL  | NULL
# 5  | has\nnewline                             | 5.0   | NULL  | NULL
# 6  | both,comma"and"quote                     | 6.0   | NULL  | NULL
# 7  | plain NULL value row                     | NULL  | NULL  | NULL
# 8  | back\slash                               | 8.0   | NULL  | NULL
# 9  | a\'b (backslash before quote)            | 9.0   | NULL  | NULL
# 10 | tail\ (trailing backslash)               | 10.0  | NULL  | NULL
# 11 | bytes blob row                           | 11.0  | NULL  | b"\xde\xad"

_SEED_SQL = """
CREATE TABLE export_test (
    id    INTEGER PRIMARY KEY,
    label TEXT,
    val   REAL,
    extra TEXT,
    blob  BLOB
);
"""

# Rows as (id, label, val, extra, blob) — extra is always NULL in the seed
_SEED_ROWS = [
    (1, "normal", 1.0, None, None),
    (2, "has,comma", 2.5, None, None),
    (3, 'has"double"quote', 3.0, None, None),
    (4, "O'Brien", 4.0, None, None),
    (5, "has\nnewline", 5.0, None, None),
    (6, 'both,comma"and"quote', 6.0, None, None),
    (7, "plain NULL value row", None, None, None),
    (8, "back\\slash", 8.0, None, None),
    (9, "a\\'b", 9.0, None, None),
    (10, "tail\\", 10.0, None, None),
    (11, "bytes blob row", 11.0, None, b"\xde\xad"),
]


def _make_repo(tmp_path: Path) -> Repository:
    tmp_path.mkdir(parents=True, exist_ok=True)
    db_path = tmp_path / "app.db"
    engine = make_engine(db_path)
    with engine.connect() as conn:
        run_migrations(conn)
    return Repository(engine)


def _seed_target(path: Path) -> None:
    with sqlite3.connect(path) as cx:
        cx.execute(_SEED_SQL)
        cx.executemany(
            "INSERT INTO export_test (id, label, val, extra, blob) VALUES (?, ?, ?, ?, ?)",
            _SEED_ROWS,
        )
        cx.commit()


def _register(repo: Repository, db_path: Path, *, read_only: bool = False) -> int:
    profile = repo.create_connection(
        ConnectionCreate(
            name="test",
            engine="sqlite",
            database=str(db_path),
            read_only=read_only,
        )
    )
    return profile.id


def _make_exporter(
    tmp_path: Path,
    target_path: Path,
    *,
    read_only: bool = False,
) -> tuple[Exporter, int, ConnectionManager]:
    repo = _make_repo(tmp_path)
    conn_id = _register(repo, target_path, read_only=read_only)
    cm = ConnectionManager(repo)
    return Exporter(cm), conn_id, cm


# ---------------------------------------------------------------------------
# CSV
# ---------------------------------------------------------------------------


def test_csv_roundtrip(tmp_path: Path) -> None:
    """csv.reader round-trip: all adversarial values survive."""
    target = tmp_path / "t.db"
    _seed_target(target)
    exporter, conn_id, cm = _make_exporter(tmp_path / "app", target)

    try:
        chunks = list(
            exporter.export_query(
                conn_id,
                "SELECT * FROM export_test ORDER BY id",
                "csv",
            )
        )
    finally:
        cm.close_all()

    output = "".join(chunks)
    reader = csv.reader(io.StringIO(output))
    all_rows = list(reader)

    # First row must be the header
    header = all_rows[0]
    assert header == ["id", "label", "val", "extra", "blob"], header

    data_rows = all_rows[1:]
    assert len(data_rows) == len(_SEED_ROWS), data_rows

    # Verify each row against seed data
    for parsed, (exp_id, exp_label, exp_val, _exp_extra, _exp_blob) in zip(
        data_rows, _SEED_ROWS, strict=True
    ):
        assert parsed[0] == str(exp_id)
        assert parsed[1] == exp_label  # adversarial value must survive
        if exp_val is None:
            assert parsed[2] == ""  # NULL → empty field
        else:
            assert float(parsed[2]) == pytest.approx(exp_val)
        assert parsed[3] == ""  # extra is always NULL


def test_csv_null_is_empty(tmp_path: Path) -> None:
    """NULL values appear as empty strings in CSV output."""
    target = tmp_path / "t.db"
    _seed_target(target)
    exporter, conn_id, cm = _make_exporter(tmp_path / "app", target)

    try:
        output = "".join(
            exporter.export_query(
                conn_id,
                "SELECT id, label, val FROM export_test WHERE id = 7",
                "csv",
            )
        )
    finally:
        cm.close_all()

    reader = csv.reader(io.StringIO(output))
    rows = list(reader)
    # rows[0] = header, rows[1] = data
    assert rows[1][2] == ""  # val is NULL


def test_csv_header_present(tmp_path: Path) -> None:
    """CSV output always starts with a header row."""
    target = tmp_path / "t.db"
    _seed_target(target)
    exporter, conn_id, cm = _make_exporter(tmp_path / "app", target)

    try:
        output = "".join(
            exporter.export_query(
                conn_id,
                "SELECT id, label FROM export_test LIMIT 1",
                "csv",
            )
        )
    finally:
        cm.close_all()

    first_line = output.split("\n")[0].strip().strip("\r")
    assert "id" in first_line
    assert "label" in first_line


def test_csv_newline_in_value(tmp_path: Path) -> None:
    """A newline embedded in a value is correctly quoted by the csv module."""
    target = tmp_path / "t.db"
    _seed_target(target)
    exporter, conn_id, cm = _make_exporter(tmp_path / "app", target)

    try:
        output = "".join(
            exporter.export_query(
                conn_id,
                "SELECT * FROM export_test WHERE id = 5",
                "csv",
            )
        )
    finally:
        cm.close_all()

    reader = csv.reader(io.StringIO(output))
    rows = list(reader)
    # rows[0] = header, rows[1] = data
    assert rows[1][1] == "has\nnewline"


# ---------------------------------------------------------------------------
# JSON
# ---------------------------------------------------------------------------


def test_json_roundtrip(tmp_path: Path) -> None:
    """json.loads on the concatenated output returns a list of correct dicts."""
    target = tmp_path / "t.db"
    _seed_target(target)
    exporter, conn_id, cm = _make_exporter(tmp_path / "app", target)

    try:
        output = "".join(
            exporter.export_query(
                conn_id,
                "SELECT * FROM export_test ORDER BY id",
                "json",
            )
        )
    finally:
        cm.close_all()

    data = json.loads(output)
    assert isinstance(data, list)
    assert len(data) == len(_SEED_ROWS)

    for obj, (exp_id, exp_label, exp_val, _, _exp_blob) in zip(data, _SEED_ROWS, strict=True):
        assert obj["id"] == exp_id
        assert obj["label"] == exp_label
        if exp_val is None:
            assert obj["val"] is None
        else:
            assert obj["val"] == pytest.approx(exp_val)


def test_json_null_is_none(tmp_path: Path) -> None:
    """NULL columns appear as JSON null (Python None after loads)."""
    target = tmp_path / "t.db"
    _seed_target(target)
    exporter, conn_id, cm = _make_exporter(tmp_path / "app", target)

    try:
        output = "".join(
            exporter.export_query(
                conn_id,
                "SELECT * FROM export_test WHERE id = 7",
                "json",
            )
        )
    finally:
        cm.close_all()

    data = json.loads(output)
    assert data[0]["val"] is None


def test_json_special_types_no_raise(tmp_path: Path) -> None:
    """The JSON encoder handles datetime, Decimal, bytes without raising."""
    from pydbplay.core.exporter import _json_default

    # Verify the encoder helpers work directly
    assert _json_default(datetime(2024, 1, 15, 12, 0, 0)) == "2024-01-15T12:00:00"
    assert _json_default(Decimal("3.14")) == "3.14"
    assert _json_default(b"\xde\xad\xbe\xef") == "deadbeef"

    # Confirm json.dumps uses the encoder without raising
    obj = {
        "dt": datetime(2024, 1, 15),
        "amount": Decimal("99.99"),
        "blob": b"\x00\xff",
    }
    result = json.dumps(obj, default=_json_default)
    decoded = json.loads(result)
    assert decoded["amount"] == "99.99"
    assert decoded["blob"] == "00ff"

    # Exercise the actual export path with a BLOB column (row 11)
    target = tmp_path / "t.db"
    _seed_target(target)
    exporter, conn_id, cm = _make_exporter(tmp_path / "app", target)
    try:
        output = "".join(
            exporter.export_query(
                conn_id,
                "SELECT id, blob FROM export_test WHERE id = 11",
                "json",
            )
        )
    finally:
        cm.close_all()

    decoded_rows = json.loads(output)  # must not raise — output is valid JSON
    assert len(decoded_rows) == 1
    assert decoded_rows[0]["blob"] == "dead"  # bytes → hex string


# ---------------------------------------------------------------------------
# SQL INSERT
# ---------------------------------------------------------------------------


def _split_sql_stmts(sql_output: str) -> list[str]:
    """Split SQL export output into individual statements.

    Each statement is terminated by ``;\n`` — we split on that boundary.
    This handles multi-line VALUES (e.g. strings containing embedded newlines)
    correctly, since ``;\n`` only appears at statement boundaries.
    """
    # Strip trailing whitespace then split: each statement ends with ";"
    # followed by a newline that was injected by the exporter.
    raw = [s.strip() for s in sql_output.split(";\n")]
    return [s for s in raw if s]


def test_sql_insert_format(tmp_path: Path) -> None:
    """Each INSERT statement is syntactically correct."""
    target = tmp_path / "t.db"
    _seed_target(target)
    exporter, conn_id, cm = _make_exporter(tmp_path / "app", target)

    try:
        output = "".join(
            exporter.export_query(
                conn_id,
                "SELECT * FROM export_test ORDER BY id",
                "sql",
                table_name="export_test",
            )
        )
    finally:
        cm.close_all()

    stmts = _split_sql_stmts(output)
    assert len(stmts) == len(_SEED_ROWS)
    for stmt in stmts:
        assert stmt.startswith('INSERT INTO "export_test"'), stmt
        assert "VALUES" in stmt


def test_sql_obrien_escaping(tmp_path: Path) -> None:
    """O'Brien must appear as 'O''Brien' in SQL (doubled single-quote)."""
    target = tmp_path / "t.db"
    _seed_target(target)
    exporter, conn_id, cm = _make_exporter(tmp_path / "app", target)

    try:
        output = "".join(
            exporter.export_query(
                conn_id,
                "SELECT * FROM export_test WHERE id = 4",
                "sql",
                table_name="export_test",
            )
        )
    finally:
        cm.close_all()

    assert "'O''Brien'" in output, f"Expected doubled quote in: {output!r}"


def test_sql_null_literal(tmp_path: Path) -> None:
    """NULL values appear as the SQL keyword NULL (unquoted)."""
    target = tmp_path / "t.db"
    _seed_target(target)
    exporter, conn_id, cm = _make_exporter(tmp_path / "app", target)

    try:
        output = "".join(
            exporter.export_query(
                conn_id,
                "SELECT * FROM export_test WHERE id = 7",
                "sql",
                table_name="export_test",
            )
        )
    finally:
        cm.close_all()

    # val is NULL for id=7
    assert " NULL" in output, f"Expected NULL keyword in: {output!r}"


def test_sql_identifiers_quoted(tmp_path: Path) -> None:
    """Column names in INSERT statements are double-quoted."""
    target = tmp_path / "t.db"
    _seed_target(target)
    exporter, conn_id, cm = _make_exporter(tmp_path / "app", target)

    try:
        output = "".join(
            exporter.export_query(
                conn_id,
                "SELECT id, label FROM export_test LIMIT 1",
                "sql",
                table_name="export_test",
            )
        )
    finally:
        cm.close_all()

    assert '"id"' in output
    assert '"label"' in output


def test_sql_roundtrip_execution(tmp_path: Path) -> None:
    """INSERT statements can be re-executed against a fresh empty SQLite DB.

    Covers all seed rows including backslash values (rows 8-10) and a BLOB row
    (row 11) to verify dialect-aware escaping keeps the SQLite path correct.
    """
    target = tmp_path / "t.db"
    _seed_target(target)
    exporter, conn_id, cm = _make_exporter(tmp_path / "app", target)

    try:
        output = "".join(
            exporter.export_query(
                conn_id,
                "SELECT * FROM export_test ORDER BY id",
                "sql",
                table_name="export_test",
            )
        )
    finally:
        cm.close_all()

    # Replay into a fresh DB — use executescript to handle multi-line values
    # (string values with embedded newlines span multiple lines in the output).
    dest = tmp_path / "dest.db"
    with sqlite3.connect(dest) as cx:
        cx.execute(_SEED_SQL.strip())
        cx.commit()
        cx.executescript(output)  # executescript handles embedded newlines safely

        rows = cx.execute("SELECT * FROM export_test ORDER BY id").fetchall()

    assert len(rows) == len(_SEED_ROWS)
    for row, (exp_id, exp_label, exp_val, _, exp_blob) in zip(rows, _SEED_ROWS, strict=True):
        assert row[0] == exp_id
        assert row[1] == exp_label  # backslash values must round-trip correctly
        if exp_val is None:
            assert row[2] is None
        else:
            assert row[2] == pytest.approx(exp_val)
        # blob column: bytes came back as bytes from SQLite
        if exp_blob is None:
            assert row[4] is None
        else:
            assert bytes(row[4]) == exp_blob


# ---------------------------------------------------------------------------
# _sql_literal unit tests — dialect-aware, no DB required
# ---------------------------------------------------------------------------


def test_sql_literal_mysql_backslash_doubled() -> None:
    """MySQL: backslash is doubled before single-quote doubling."""
    # Plain backslash → doubled
    assert _sql_literal("back\\slash", "mysql") == "'back\\\\slash'"
    # Backslash immediately before a quote (a\'b → a\\''b in SQL):
    # step 1 escape \: a\\' b  →  step 2 double ': a\\'' b
    assert _sql_literal("a\\'b", "mysql") == "'a\\\\''b'"
    # Trailing backslash: must be doubled so the closing quote isn't escaped
    assert _sql_literal("tail\\", "mysql") == "'tail\\\\'"


def test_sql_literal_sqlite_backslash_not_doubled() -> None:
    """SQLite (and postgres): backslash has no special meaning — do NOT touch it."""
    assert _sql_literal("back\\slash", "sqlite") == "'back\\slash'"
    assert _sql_literal("tail\\", "sqlite") == "'tail\\'"


def test_sql_literal_postgres_backslash_not_doubled() -> None:
    """Postgres standard strings: backslash has no special meaning."""
    assert _sql_literal("back\\slash", "postgres") == "'back\\slash'"
    assert _sql_literal("tail\\", "postgres") == "'tail\\'"


def test_sql_literal_mysql_single_quote_doubled() -> None:
    """MySQL: single-quote is still doubled (after backslash is handled)."""
    assert _sql_literal("O'Brien", "mysql") == "'O''Brien'"


def test_sql_literal_sqlite_single_quote_doubled() -> None:
    """SQLite: standard SQL single-quote doubling."""
    assert _sql_literal("O'Brien", "sqlite") == "'O''Brien'"


def test_sql_literal_bytes_sqlite_mysql() -> None:
    """sqlite and mysql: bytes → X'<hex>'."""
    assert _sql_literal(b"\xde\xad", "sqlite") == "X'dead'"
    assert _sql_literal(b"\xde\xad", "mysql") == "X'dead'"


def test_sql_literal_bytes_postgres() -> None:
    """postgres: bytes → '\\x<hex>' (bytea escape syntax)."""
    assert _sql_literal(b"\xde\xad", "postgres") == "'\\xdead'"


def test_sql_literal_nonfinite_float_null() -> None:
    """Non-finite floats emit NULL in all dialects (syntax error otherwise)."""
    for dialect in ("sqlite", "mysql", "postgres"):
        assert _sql_literal(math.inf, dialect) == "NULL", dialect
        assert _sql_literal(-math.inf, dialect) == "NULL", dialect
        assert _sql_literal(math.nan, dialect) == "NULL", dialect


def test_sql_literal_regular_float_preserved() -> None:
    """Finite floats are emitted as numeric literals (not NULL)."""
    result = _sql_literal(3.14, "sqlite")
    assert result != "NULL"
    assert float(result) == pytest.approx(3.14)


# ---------------------------------------------------------------------------
# Streaming: small chunk_size yields complete output
# ---------------------------------------------------------------------------


def test_streaming_small_chunk_size(tmp_path: Path) -> None:
    """chunk_size=2 over all seed rows still produces complete, correct CSV output."""
    target = tmp_path / "t.db"
    _seed_target(target)
    exporter, conn_id, cm = _make_exporter(tmp_path / "app", target)

    try:
        chunks = list(
            exporter.export_query(
                conn_id,
                "SELECT * FROM export_test ORDER BY id",
                "csv",
                chunk_size=2,
            )
        )
    finally:
        cm.close_all()

    output = "".join(chunks)
    reader = csv.reader(io.StringIO(output))
    all_rows = list(reader)

    # header + N data rows
    assert len(all_rows) == len(_SEED_ROWS) + 1


def test_streaming_small_chunk_json(tmp_path: Path) -> None:
    """chunk_size=2 over all seed rows still produces valid JSON with all rows."""
    target = tmp_path / "t.db"
    _seed_target(target)
    exporter, conn_id, cm = _make_exporter(tmp_path / "app", target)

    try:
        output = "".join(
            exporter.export_query(
                conn_id,
                "SELECT * FROM export_test ORDER BY id",
                "json",
                chunk_size=2,
            )
        )
    finally:
        cm.close_all()

    data = json.loads(output)
    assert len(data) == len(_SEED_ROWS)


def test_streaming_small_chunk_sql(tmp_path: Path) -> None:
    """chunk_size=2 over all seed rows still produces the correct number of INSERT statements."""
    target = tmp_path / "t.db"
    _seed_target(target)
    exporter, conn_id, cm = _make_exporter(tmp_path / "app", target)

    try:
        output = "".join(
            exporter.export_query(
                conn_id,
                "SELECT * FROM export_test ORDER BY id",
                "sql",
                table_name="export_test",
                chunk_size=2,
            )
        )
    finally:
        cm.close_all()

    stmts = _split_sql_stmts(output)
    assert len(stmts) == len(_SEED_ROWS)


# ---------------------------------------------------------------------------
# export_table whitelisting
# ---------------------------------------------------------------------------


def test_export_table_unknown_raises(tmp_path: Path) -> None:
    """export_table raises UnknownIdentifierError for an unknown table."""
    target = tmp_path / "t.db"
    _seed_target(target)
    exporter, conn_id, cm = _make_exporter(tmp_path / "app", target)

    try:
        with pytest.raises(UnknownIdentifierError):
            list(exporter.export_table(conn_id, "nonexistent_table", "csv"))
    finally:
        cm.close_all()


def test_export_table_malicious_name_raises(tmp_path: Path) -> None:
    """export_table raises UnknownIdentifierError for SQL-injection-style names."""
    target = tmp_path / "t.db"
    _seed_target(target)
    exporter, conn_id, cm = _make_exporter(tmp_path / "app", target)

    try:
        with pytest.raises(UnknownIdentifierError):
            list(exporter.export_table(conn_id, "export_test; DROP TABLE export_test--", "csv"))
    finally:
        cm.close_all()


def test_export_table_known_table_works(tmp_path: Path) -> None:
    """export_table succeeds for a valid, existing table name."""
    target = tmp_path / "t.db"
    _seed_target(target)
    exporter, conn_id, cm = _make_exporter(tmp_path / "app", target)

    try:
        output = "".join(exporter.export_table(conn_id, "export_test", "csv"))
    finally:
        cm.close_all()

    reader = csv.reader(io.StringIO(output))
    rows = list(reader)
    # 1 header + 7 data rows
    assert len(rows) == len(_SEED_ROWS) + 1


def test_export_table_readonly_connection_allowed(tmp_path: Path) -> None:
    """export_table on a read-only connection succeeds (SELECT is always allowed)."""
    target = tmp_path / "t.db"
    _seed_target(target)
    exporter, conn_id, cm = _make_exporter(tmp_path / "app", target, read_only=True)

    try:
        output = "".join(exporter.export_table(conn_id, "export_test", "json"))
    finally:
        cm.close_all()

    data = json.loads(output)
    assert len(data) == len(_SEED_ROWS)


# ---------------------------------------------------------------------------
# Empty result set
# ---------------------------------------------------------------------------


def test_empty_result_csv(tmp_path: Path) -> None:
    """Empty result set: CSV yields nothing (no rows to derive header from)."""
    target = tmp_path / "t.db"
    _seed_target(target)
    exporter, conn_id, cm = _make_exporter(tmp_path / "app", target)

    try:
        output = "".join(
            exporter.export_query(
                conn_id,
                "SELECT * FROM export_test WHERE 1=0",
                "csv",
            )
        )
    finally:
        cm.close_all()

    # No rows → execute_stream yields nothing → no header written
    assert output == ""


def test_empty_result_json(tmp_path: Path) -> None:
    """Empty result set: JSON yields '[]'."""
    target = tmp_path / "t.db"
    _seed_target(target)
    exporter, conn_id, cm = _make_exporter(tmp_path / "app", target)

    try:
        output = "".join(
            exporter.export_query(
                conn_id,
                "SELECT * FROM export_test WHERE 1=0",
                "json",
            )
        )
    finally:
        cm.close_all()

    data = json.loads(output)
    assert data == []


def test_empty_result_sql(tmp_path: Path) -> None:
    """Empty result set: SQL yields no INSERT statements."""
    target = tmp_path / "t.db"
    _seed_target(target)
    exporter, conn_id, cm = _make_exporter(tmp_path / "app", target)

    try:
        output = "".join(
            exporter.export_query(
                conn_id,
                "SELECT * FROM export_test WHERE 1=0",
                "sql",
                table_name="export_test",
            )
        )
    finally:
        cm.close_all()

    stmts = _split_sql_stmts(output)
    assert stmts == []


# ---------------------------------------------------------------------------
# Unknown format
# ---------------------------------------------------------------------------


def test_unknown_format_raises(tmp_path: Path) -> None:
    """export_query raises ExporterError for an unrecognised format string."""
    target = tmp_path / "t.db"
    _seed_target(target)
    exporter, conn_id, cm = _make_exporter(tmp_path / "app", target)

    try:
        with pytest.raises(ExporterError, match="Unknown export format"):
            list(exporter.export_query(conn_id, "SELECT 1", "xlsx"))  # type: ignore[arg-type]
    finally:
        cm.close_all()
