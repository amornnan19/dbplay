"""Export query results to CSV, JSON, or SQL INSERT statements.

Per SPEC §9:
- Use ``execute_stream`` (chunked cursor) — never fetchall() on large tables.
- Whitelist every table/schema identifier before embedding it in SQL.
- The adapter's read-only guard allows SELECT on read-only connections, so
  export from a read-only connection works correctly.

Format contracts
----------------
- CSV: stdlib ``csv`` module — correct quoting of commas/quotes/newlines.
       NULL → empty field.  Bool → True/False.
- JSON: streaming array ``[\\n{...},\\n{...}\\n]``.  NULL → JSON null.
        Bool → true/false.  Decimal → JSON string (loss-free representation).
        datetime/bytes handled by custom encoder.
        Non-finite floats (inf/nan) → JSON null (portable; bare Infinity/NaN
        tokens are not valid JSON per RFC 8259).
- SQL: one ``INSERT INTO <quoted> (<cols>) VALUES (<literals>);`` per row.
       NULL → SQL NULL.  str → single-quoted with dialect-aware escaping:
         mysql  → backslash doubled FIRST, then single-quote doubled.
         sqlite/postgres → single-quote doubled only (standard SQL strings).
       Bool → 1/0.  bytes → ``X'<hex>'`` (sqlite/mysql) or ``'\\x<hex>'``
       (postgres bytea).  datetime/date → ISO-8601 single-quoted string.
       Non-finite floats → NULL (portable; inf/nan are syntax errors in all
       dialects).

Error contract
--------------
- Unknown format: raises ``ExporterError``.
- Unknown/malicious table in ``export_table``: ``UnknownIdentifierError``
  propagates from ``adapter.validate_identifier``.
- Non-SELECT SQL on a read-only adapter: ``ReadOnlyViolationError`` propagates
  from the adapter layer.  The exporter does NOT add an extra guard.
"""

import csv
import io
import json
import math
from collections.abc import Iterator
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

from pydbplay.adapters.base import (
    DBAdapter,
    ReadOnlyViolationError,  # noqa: F401 — re-exported for callers
    UnknownIdentifierError,  # noqa: F401 — re-exported for callers
)
from pydbplay.core.connection_manager import ConnectionManager

# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------

ExportFormat = Literal["csv", "json", "sql"]


class ExporterError(Exception):
    """Raised by Exporter for logical errors (e.g. unknown format)."""


# ---------------------------------------------------------------------------
# Value helpers
# ---------------------------------------------------------------------------


def _csv_value(v: Any) -> str:
    """Convert *v* to a CSV cell string.

    None → empty string; bool → "True"/"False"; Decimal → str; bytes → hex;
    datetime/date → ISO-8601; all others → str(v).
    """
    if v is None:
        return ""
    if isinstance(v, bool):
        return str(v)  # "True" / "False"
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    if isinstance(v, Decimal):
        return str(v)
    if isinstance(v, bytes):
        return v.hex()
    return str(v)


def _sql_literal(v: Any, dialect: str = "sqlite") -> str:
    """Render *v* as a SQL literal suitable for embedding in an INSERT VALUES list.

    Args:
        v: Python value to render.
        dialect: Adapter dialect string — affects string escaping and bytes
            representation.  Supported values: ``"sqlite"``, ``"postgres"``,
            ``"mysql"``.

    Returns:
        A SQL literal string safe for embedding inside a VALUES clause.

    Escaping rules for string values:
        - ``mysql``: backslash is an escape character by default, so backslash
          must be doubled *first* (``\\`` → ``\\\\``), then single-quotes are
          doubled (``'`` → ``''``).
        - ``sqlite`` / ``postgres``: standard SQL strings — only single-quotes
          are doubled; backslash has no special meaning.

    Bytes representation:
        - ``sqlite`` / ``mysql``: ``X'<hex>'`` hex literal.
        - ``postgres``: ``'\\x<hex>'`` bytea escape syntax.

    Non-finite floats:
        ``math.inf``, ``-math.inf``, and ``math.nan`` are exported as ``NULL``.
        All three values are syntax errors in every major SQL dialect, so NULL
        is the only portable representation.  See module docstring.
    """
    if v is None:
        return "NULL"
    if isinstance(v, bool):
        return "1" if v else "0"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        if math.isinf(v) or math.isnan(v):
            return "NULL"
        return repr(v)
    if isinstance(v, Decimal):
        return str(v)
    if isinstance(v, (datetime, date)):
        return "'" + v.isoformat() + "'"
    if isinstance(v, bytes):
        hex_val = v.hex()
        if dialect == "postgres":
            return f"'\\x{hex_val}'"
        # sqlite and mysql both use X'...' hex literals
        return f"X'{hex_val}'"
    # str (and anything else) — escape with dialect-aware rules
    text = str(v)
    if dialect == "mysql":
        # MySQL treats backslash as an escape character inside string literals
        # (default sql_mode). Escape backslash FIRST, then single-quotes.
        text = text.replace("\\", "\\\\")
    text = text.replace("'", "''")
    return f"'{text}'"


def _json_default(obj: Any) -> Any:
    """JSON encoder fallback for types json.dumps can't handle natively.

    Covers: datetime, date, Decimal, bytes.  Raises TypeError for unknown
    types (json.dumps propagates that as TypeError to callers).

    Note: Non-finite float values (inf/-inf/nan) are not routed here — they
    are handled by a pre-pass in ``_stream_json`` that replaces them with None
    before serialisation, ensuring RFC 8259-valid output.  Decimal values are
    returned as strings to preserve precision without loss.
    """
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, Decimal):
        return str(obj)
    if isinstance(obj, bytes):
        return obj.hex()
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


# ---------------------------------------------------------------------------
# Exporter
# ---------------------------------------------------------------------------


class Exporter:
    """Streams query results to CSV, JSON, or SQL INSERT format.

    Uses ``DBAdapter.execute_stream`` (server-side cursor, chunk_size rows at a
    time) so large tables do not exhaust memory.

    Args:
        connection_manager: Provides live DBAdapter instances keyed by profile id.
    """

    def __init__(self, connection_manager: ConnectionManager) -> None:
        self._connection_manager = connection_manager

    # ------------------------------------------------------------------
    # Public entry points
    # ------------------------------------------------------------------

    def export_query(
        self,
        conn_id: int,
        sql: str,
        fmt: ExportFormat,
        *,
        table_name: str = "query_result",
        chunk_size: int = 1000,
    ) -> Iterator[str]:
        """Stream the result of *sql* in *fmt* format.

        Args:
            conn_id: Primary key of the ConnectionProfile.
            sql: SELECT statement to export.  The adapter's read-only guard
                 will reject non-SELECT SQL on read-only connections.
            fmt: One of ``"csv"``, ``"json"``, or ``"sql"``.
            table_name: INSERT target name used in SQL format (arbitrary queries
                        have no intrinsic table name).
            chunk_size: Rows fetched per cursor batch.

        Yields:
            String chunks forming the complete export document.

        Raises:
            ExporterError: If *fmt* is not a recognised export format.
            ReadOnlyViolationError: Propagated from the adapter when the
                connection is read-only and *sql* is not a SELECT.
            AdapterError: Propagated from the adapter on DB-level errors.
            KeyError: If *conn_id* does not exist.
        """
        adapter: DBAdapter = self._connection_manager.get_adapter(conn_id)

        if fmt == "csv":
            yield from self._stream_csv(adapter, sql, chunk_size=chunk_size)
        elif fmt == "json":
            yield from self._stream_json(adapter, sql, chunk_size=chunk_size)
        elif fmt == "sql":
            yield from self._stream_sql(adapter, sql, table_name, chunk_size=chunk_size)
        else:
            raise ExporterError(f"Unknown export format: {fmt!r}. Must be 'csv', 'json', or 'sql'.")

    def export_table(
        self,
        conn_id: int,
        table: str,
        fmt: ExportFormat,
        *,
        schema: str | None = None,
        chunk_size: int = 1000,
    ) -> Iterator[str]:
        """Stream all rows from *table* in *fmt* format.

        *table* is whitelisted against the live schema before building the
        ``SELECT * FROM`` query — raises ``UnknownIdentifierError`` for unknown
        or malicious identifiers.

        Args:
            conn_id: Primary key of the ConnectionProfile.
            table: Unquoted table name.  Whitelisted against the live schema.
            fmt: One of ``"csv"``, ``"json"``, or ``"sql"``.
            schema: Schema name; None means the engine default.
            chunk_size: Rows fetched per cursor batch.

        Yields:
            String chunks forming the complete export document.

        Raises:
            UnknownIdentifierError: If *table* is not in the live schema.
            ExporterError: If *fmt* is not a recognised export format.
            ReadOnlyViolationError: Propagated from the adapter (read-only
                connections can still export — SELECT is allowed).
            KeyError: If *conn_id* does not exist.
        """
        adapter: DBAdapter = self._connection_manager.get_adapter(conn_id)

        # Whitelist the table name against the live schema BEFORE building SQL.
        known_tables: set[str] = {t.name for t in adapter.list_tables(schema)}
        quoted_table: str = adapter.validate_identifier(table, known=known_tables)

        sql = f"SELECT * FROM {quoted_table}"

        if fmt == "csv":
            yield from self._stream_csv(adapter, sql, chunk_size=chunk_size)
        elif fmt == "json":
            yield from self._stream_json(adapter, sql, chunk_size=chunk_size)
        elif fmt == "sql":
            # Use the real (whitelisted, quoted) table name as the INSERT target.
            yield from self._stream_sql(adapter, sql, table, chunk_size=chunk_size)
        else:
            raise ExporterError(f"Unknown export format: {fmt!r}. Must be 'csv', 'json', or 'sql'.")

    # ------------------------------------------------------------------
    # Format implementation helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _stream_csv(
        adapter: DBAdapter,
        sql: str,
        *,
        chunk_size: int,
    ) -> Iterator[str]:
        """Yield CSV-formatted text chunks.

        Writes to an ``io.StringIO`` buffer per chunk and yields the buffer's
        contents.  The stdlib ``csv`` module handles all quoting correctly.

        Column names come from the first chunk's dict keys (preserves column
        order as returned by the DB).  An empty result yields only the header
        row when column names can be inferred — which requires at least one row
        or the column names from the stream.  Since ``execute_stream`` yields
        chunks of dicts (keys = columns), column names are available only if at
        least one chunk arrives.  For a totally empty result set no header is
        yielded either (the stream is exhausted immediately with no data).
        """
        buf = io.StringIO()
        writer = csv.writer(buf)

        header_written = False

        for chunk in adapter.execute_stream(sql, chunk_size=chunk_size):
            if not chunk:
                continue  # empty batch — shouldn't happen but be defensive

            if not header_written:
                columns = list(chunk[0].keys())
                writer.writerow(columns)
                header_written = True
                yield buf.getvalue()
                buf.seek(0)
                buf.truncate(0)

            for row_dict in chunk:
                writer.writerow([_csv_value(v) for v in row_dict.values()])

            yield buf.getvalue()
            buf.seek(0)
            buf.truncate(0)

    @staticmethod
    def _stream_json(
        adapter: DBAdapter,
        sql: str,
        *,
        chunk_size: int,
    ) -> Iterator[str]:
        """Yield a streaming JSON array.

        Format: ``[\\n{...},\\n{...}\\n]`` — never builds the whole list in memory.
        The first row is yielded without a leading comma; subsequent rows with one.

        Non-finite float values (inf/-inf/nan) are replaced with ``None`` before
        serialisation so the output is always valid per RFC 8259 (bare
        ``Infinity`` / ``NaN`` tokens are not valid JSON).
        """
        yield "[\n"

        first_row = True

        for chunk in adapter.execute_stream(sql, chunk_size=chunk_size):
            for row_dict in chunk:
                # Replace non-finite floats with None for RFC 8259 compliance.
                safe_row = {
                    k: (None if isinstance(v, float) and (math.isinf(v) or math.isnan(v)) else v)
                    for k, v in row_dict.items()
                }
                row_json = json.dumps(safe_row, default=_json_default)
                if first_row:
                    yield row_json
                    first_row = False
                else:
                    yield ",\n" + row_json

        yield "\n]"

    @staticmethod
    def _stream_sql(
        adapter: DBAdapter,
        sql: str,
        table_name: str,
        *,
        chunk_size: int,
    ) -> Iterator[str]:
        """Yield one SQL INSERT statement per row.

        Format::

            INSERT INTO "table" ("col1", "col2") VALUES ('val1', 42);

        Identifiers are quoted via ``adapter.quote_identifier``.
        Values are literalised via ``_sql_literal`` with the adapter's dialect,
        so string/bytes escaping is correct for each target engine — never
        parameterised (the INSERT statements are for human consumption, not
        re-execution through the ORM).
        """
        quoted_table = adapter.quote_identifier(table_name)
        dialect = adapter.dialect

        columns: list[str] | None = None
        quoted_cols_clause: str = ""

        for chunk in adapter.execute_stream(sql, chunk_size=chunk_size):
            for row_dict in chunk:
                if columns is None:
                    columns = list(row_dict.keys())
                    quoted_cols = [adapter.quote_identifier(c) for c in columns]
                    quoted_cols_clause = ", ".join(quoted_cols)

                values_clause = ", ".join(_sql_literal(v, dialect) for v in row_dict.values())
                yield (
                    f"INSERT INTO {quoted_table} ({quoted_cols_clause}) VALUES ({values_clause});\n"
                )
