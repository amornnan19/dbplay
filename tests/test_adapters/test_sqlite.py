"""Tests for SQLiteAdapter — real SQLite file via tmp_path (NOT :memory:)."""

from pathlib import Path

import pytest

from pydbplay.adapters.base import AdapterError, ReadOnlyViolationError, UnknownIdentifierError
from pydbplay.adapters.sqlite import SQLiteAdapter

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    """Return path to a seeded SQLite file in a temporary directory."""
    path = tmp_path / "test.db"
    return path


@pytest.fixture()
def adapter(db_path: Path) -> SQLiteAdapter:
    """An SQLiteAdapter with a seeded schema."""
    a = SQLiteAdapter(str(db_path))
    _seed(a)
    return a


@pytest.fixture()
def ro_adapter(db_path: Path) -> SQLiteAdapter:
    """A read-only SQLiteAdapter with the same seeded schema."""
    # Seed via a writable adapter first
    seed_adapter = SQLiteAdapter(str(db_path))
    _seed(seed_adapter)
    return SQLiteAdapter(str(db_path), read_only=True)


def _seed(a: SQLiteAdapter) -> None:
    """Create tables and populate rows for tests."""
    a.execute(
        """
        CREATE TABLE IF NOT EXISTS department (
            dept_id   INTEGER PRIMARY KEY,
            dept_name TEXT    NOT NULL DEFAULT 'unknown'
        )
        """
    )
    a.execute(
        """
        CREATE TABLE IF NOT EXISTS employee (
            emp_id   INTEGER NOT NULL,
            emp_name TEXT    NOT NULL,
            dept_id  INTEGER,
            PRIMARY KEY (emp_id),
            FOREIGN KEY (dept_id) REFERENCES department(dept_id)
                ON DELETE SET NULL ON UPDATE CASCADE
        )
        """
    )
    a.execute(
        "CREATE INDEX IF NOT EXISTS idx_employee_dept ON employee(dept_id)"
    )
    # Insert departments
    for i in range(1, 4):
        a.execute(
            "INSERT OR IGNORE INTO department(dept_id, dept_name) VALUES (:id, :name)",
            {"id": i, "name": f"Dept{i}"},
        )
    # Insert 5 employees (to exercise chunk boundaries)
    for i in range(1, 6):
        a.execute(
            "INSERT OR IGNORE INTO employee(emp_id, emp_name, dept_id) VALUES (:eid, :name, :did)",
            {"eid": i, "name": f"Emp{i}", "did": ((i - 1) % 3) + 1},
        )


# ---------------------------------------------------------------------------
# test_connection
# ---------------------------------------------------------------------------


def test_connection_valid(adapter: SQLiteAdapter) -> None:
    assert adapter.test_connection() is True


def test_connection_memory() -> None:
    # SQLite is permissive — use :memory: to confirm test_connection returns True.
    good = SQLiteAdapter(":memory:")
    assert good.test_connection() is True


# ---------------------------------------------------------------------------
# list_schemas
# ---------------------------------------------------------------------------


def test_list_schemas(adapter: SQLiteAdapter) -> None:
    schemas = adapter.list_schemas()
    assert "main" in schemas


# ---------------------------------------------------------------------------
# list_tables
# ---------------------------------------------------------------------------


def test_list_tables_returns_seeded_tables(adapter: SQLiteAdapter) -> None:
    tables = adapter.list_tables()
    names = {t.name for t in tables}
    assert "department" in names
    assert "employee" in names


def test_list_tables_excludes_sqlite_internals(adapter: SQLiteAdapter) -> None:
    tables = adapter.list_tables()
    for t in tables:
        assert not t.name.startswith("sqlite_")


def test_list_tables_schema_field(adapter: SQLiteAdapter) -> None:
    tables = adapter.list_tables()
    for t in tables:
        assert t.schema_name == "main"


def test_list_tables_type(adapter: SQLiteAdapter) -> None:
    tables = adapter.list_tables()
    types = {t.name: t.table_type for t in tables}
    assert types["department"] == "BASE TABLE"
    assert types["employee"] == "BASE TABLE"


# ---------------------------------------------------------------------------
# describe_table
# ---------------------------------------------------------------------------


def test_describe_table_columns_department(adapter: SQLiteAdapter) -> None:
    schema = adapter.describe_table("department")
    col_map = {c.name: c for c in schema.columns}

    assert "dept_id" in col_map
    assert "dept_name" in col_map

    dept_id = col_map["dept_id"]
    assert dept_id.is_primary_key is True

    dept_name = col_map["dept_name"]
    assert dept_name.is_nullable is False
    assert dept_name.default_value == "'unknown'"


def test_describe_table_columns_employee(adapter: SQLiteAdapter) -> None:
    schema = adapter.describe_table("employee")
    col_map = {c.name: c for c in schema.columns}

    assert "emp_id" in col_map
    assert "emp_name" in col_map
    assert "dept_id" in col_map

    assert col_map["emp_id"].is_primary_key is True
    assert col_map["emp_name"].is_nullable is False


def test_describe_table_index(adapter: SQLiteAdapter) -> None:
    schema = adapter.describe_table("employee")
    idx_names = {i.name for i in schema.indexes}
    assert "idx_employee_dept" in idx_names

    emp_dept_idx = next(i for i in schema.indexes if i.name == "idx_employee_dept")
    assert "dept_id" in emp_dept_idx.columns


def test_describe_table_foreign_key(adapter: SQLiteAdapter) -> None:
    schema = adapter.describe_table("employee")
    assert len(schema.foreign_keys) == 1
    fk = schema.foreign_keys[0]
    assert fk.ref_table == "department"
    assert "dept_id" in fk.columns
    assert "dept_id" in fk.ref_columns


def test_describe_table_schema_name(adapter: SQLiteAdapter) -> None:
    schema = adapter.describe_table("department")
    assert schema.schema_name == "main"
    assert schema.name == "department"


# ---------------------------------------------------------------------------
# get_pk_columns
# ---------------------------------------------------------------------------


def test_get_pk_columns_single(adapter: SQLiteAdapter) -> None:
    pks = adapter.get_pk_columns("department")
    assert pks == ["dept_id"]


def test_get_pk_columns_employee(adapter: SQLiteAdapter) -> None:
    pks = adapter.get_pk_columns("employee")
    assert pks == ["emp_id"]


# ---------------------------------------------------------------------------
# execute — SELECT
# ---------------------------------------------------------------------------


def test_execute_select_returns_rows(adapter: SQLiteAdapter) -> None:
    result = adapter.execute("SELECT * FROM department ORDER BY dept_id")
    assert result.columns == ["dept_id", "dept_name"]
    assert len(result.rows) == 3
    assert result.row_count == 3
    # rows are lists
    assert result.rows[0] == [1, "Dept1"]


def test_execute_select_with_params(adapter: SQLiteAdapter) -> None:
    result = adapter.execute(
        "SELECT dept_name FROM department WHERE dept_id = :id", {"id": 2}
    )
    assert result.rows == [["Dept2"]]


# ---------------------------------------------------------------------------
# execute — INSERT / rowcount
# ---------------------------------------------------------------------------


def test_execute_insert_reports_affected(adapter: SQLiteAdapter) -> None:
    result = adapter.execute(
        "INSERT INTO department(dept_id, dept_name) VALUES (:id, :name)",
        {"id": 99, "name": "NewDept"},
    )
    assert result.columns == []
    assert result.row_count == 1

    # Verify the row was committed
    check = adapter.execute(
        "SELECT dept_name FROM department WHERE dept_id = :id", {"id": 99}
    )
    assert check.rows == [["NewDept"]]


# ---------------------------------------------------------------------------
# execute — duration_ms present
# ---------------------------------------------------------------------------


def test_execute_duration_ms(adapter: SQLiteAdapter) -> None:
    result = adapter.execute("SELECT 1")
    assert isinstance(result.duration_ms, int)
    assert result.duration_ms >= 0


# ---------------------------------------------------------------------------
# execute_stream — chunked iteration
# ---------------------------------------------------------------------------


def test_execute_stream_all_rows(adapter: SQLiteAdapter) -> None:
    all_rows: list[dict] = []
    for chunk in adapter.execute_stream("SELECT * FROM employee ORDER BY emp_id"):
        assert isinstance(chunk, list)
        all_rows.extend(chunk)
    assert len(all_rows) == 5
    assert all_rows[0]["emp_id"] == 1


def test_execute_stream_chunk_boundaries(adapter: SQLiteAdapter) -> None:
    """5 rows with chunk_size=2 should yield chunks of sizes [2, 2, 1]."""
    chunks = list(
        adapter.execute_stream(
            "SELECT * FROM employee ORDER BY emp_id", chunk_size=2
        )
    )
    assert len(chunks) == 3
    assert len(chunks[0]) == 2
    assert len(chunks[1]) == 2
    assert len(chunks[2]) == 1


def test_execute_stream_returns_dicts(adapter: SQLiteAdapter) -> None:
    chunks = list(
        adapter.execute_stream("SELECT * FROM employee ORDER BY emp_id", chunk_size=10)
    )
    assert len(chunks) == 1
    row = chunks[0][0]
    assert isinstance(row, dict)
    assert "emp_id" in row
    assert "emp_name" in row


# ---------------------------------------------------------------------------
# quote_identifier
# ---------------------------------------------------------------------------


def test_quote_identifier_basic(adapter: SQLiteAdapter) -> None:
    assert adapter.quote_identifier("my_table") == '"my_table"'


def test_quote_identifier_with_embedded_double_quote(adapter: SQLiteAdapter) -> None:
    # An identifier containing a " should be escaped as ""
    assert adapter.quote_identifier('say"hello') == '"say""hello"'


def test_quote_identifier_empty(adapter: SQLiteAdapter) -> None:
    assert adapter.quote_identifier("") == '""'


# ---------------------------------------------------------------------------
# validate_identifier
# ---------------------------------------------------------------------------


def test_validate_identifier_known(adapter: SQLiteAdapter) -> None:
    result = adapter.validate_identifier("my_col", known={"my_col", "other_col"})
    assert result == '"my_col"'


def test_validate_identifier_unknown_raises(adapter: SQLiteAdapter) -> None:
    with pytest.raises(UnknownIdentifierError):
        adapter.validate_identifier("evil_col", known={"my_col"})


# ---------------------------------------------------------------------------
# read-only enforcement
# ---------------------------------------------------------------------------


def test_readonly_select_allowed(ro_adapter: SQLiteAdapter) -> None:
    result = ro_adapter.execute("SELECT * FROM department")
    assert result.row_count == 3


def test_readonly_insert_rejected(ro_adapter: SQLiteAdapter) -> None:
    with pytest.raises(ReadOnlyViolationError):
        ro_adapter.execute(
            "INSERT INTO department(dept_id, dept_name) VALUES (100, 'X')"
        )


def test_readonly_update_rejected(ro_adapter: SQLiteAdapter) -> None:
    with pytest.raises(ReadOnlyViolationError):
        ro_adapter.execute("UPDATE department SET dept_name='Y' WHERE dept_id=1")


def test_readonly_delete_rejected(ro_adapter: SQLiteAdapter) -> None:
    with pytest.raises(ReadOnlyViolationError):
        ro_adapter.execute("DELETE FROM department WHERE dept_id=1")


def test_readonly_create_table_rejected(ro_adapter: SQLiteAdapter) -> None:
    with pytest.raises(ReadOnlyViolationError):
        ro_adapter.execute("CREATE TABLE blocked (id INTEGER PRIMARY KEY)")


def test_readonly_alter_table_rejected(ro_adapter: SQLiteAdapter) -> None:
    with pytest.raises(ReadOnlyViolationError):
        ro_adapter.execute("ALTER TABLE department ADD COLUMN extra TEXT")


def test_readonly_replace_into_rejected(ro_adapter: SQLiteAdapter) -> None:
    with pytest.raises(ReadOnlyViolationError):
        ro_adapter.execute(
            "REPLACE INTO department(dept_id, dept_name) VALUES (1, 'X')"
        )


def test_readonly_attach_rejected(ro_adapter: SQLiteAdapter) -> None:
    with pytest.raises(ReadOnlyViolationError):
        ro_adapter.execute("ATTACH DATABASE ':memory:' AS extra")


def test_readonly_write_pragma_rejected(ro_adapter: SQLiteAdapter, db_path: Path) -> None:
    """PRAGMA user_version = 1 must be rejected in read-only mode and must NOT persist."""
    with pytest.raises(ReadOnlyViolationError):
        ro_adapter.execute("PRAGMA user_version = 1")

    # Confirm via a fresh writable adapter that user_version is still 0
    fresh = SQLiteAdapter(str(db_path))
    result = fresh.execute("PRAGMA user_version")
    assert result.rows == [[0]]


def test_readonly_pragma_table_info_allowed(ro_adapter: SQLiteAdapter) -> None:
    """PRAGMA table_info on a seeded table must succeed in read-only mode."""
    result = ro_adapter.execute("PRAGMA table_info(department)")
    assert result.row_count > 0


def test_readonly_explain_select_allowed(ro_adapter: SQLiteAdapter) -> None:
    """EXPLAIN SELECT must be allowed in read-only mode."""
    result = ro_adapter.execute("EXPLAIN SELECT * FROM department")
    assert result.row_count > 0


def test_readonly_explain_query_plan_select_allowed(ro_adapter: SQLiteAdapter) -> None:
    """EXPLAIN QUERY PLAN SELECT must be allowed in read-only mode."""
    result = ro_adapter.execute("EXPLAIN QUERY PLAN SELECT * FROM department")
    assert result.row_count > 0


# ---------------------------------------------------------------------------
# execute_stream — read-only guard + DDL guard
# ---------------------------------------------------------------------------


def test_execute_stream_ddl_raises_on_readonly(ro_adapter: SQLiteAdapter, db_path: Path) -> None:
    """execute_stream('CREATE TABLE') on a read-only adapter must raise ReadOnlyViolationError
    and must NOT create the table."""
    with pytest.raises(ReadOnlyViolationError):
        # Consume the generator to trigger the guard
        list(ro_adapter.execute_stream("CREATE TABLE leaked (a INTEGER)"))

    # Confirm table was NOT created
    fresh = SQLiteAdapter(str(db_path))
    tables = {t.name for t in fresh.list_tables()}
    assert "leaked" not in tables


def test_execute_stream_ddl_raises_on_writable(adapter: SQLiteAdapter, db_path: Path) -> None:
    """execute_stream('CREATE TABLE') on a writable adapter must raise AdapterError
    and must NOT create the table."""
    with pytest.raises(AdapterError):
        list(adapter.execute_stream("CREATE TABLE leaked2 (a INTEGER)"))

    # Confirm table was NOT created (DDL did not run)
    fresh = SQLiteAdapter(str(db_path))
    tables = {t.name for t in fresh.list_tables()}
    assert "leaked2" not in tables
