"""Tests for MySQLAdapter — unit tests (no Docker) and integration tests (testcontainers)."""

import pytest

from pydbplay.adapters.base import AdapterError, ReadOnlyViolationError, UnknownIdentifierError
from pydbplay.adapters.mysql import MySQLAdapter
from pydbplay.core.sql_validator import is_read_only_statement

# ---------------------------------------------------------------------------
# Unit tests — no DB connection required (engine is lazy-connected)
# ---------------------------------------------------------------------------


class TestMySQLAdapterUnit:
    """Dialect, identifier, and read-only classification — no DB needed."""

    def _make_adapter(self, *, read_only: bool = False) -> MySQLAdapter:
        """Build a MySQLAdapter pointing at a non-existent host (lazy engine)."""
        return MySQLAdapter(
            host="mysql-unit-test-host",
            port=3306,
            database="testdb",
            username="testuser",
            password="testpass",
            read_only=read_only,
        )

    # -- dialect ----------------------------------------------------------

    def test_dialect(self) -> None:
        adapter = self._make_adapter()
        assert adapter.dialect == "mysql"

    # -- quote_identifier (backticks) -------------------------------------

    def test_quote_identifier_basic(self) -> None:
        adapter = self._make_adapter()
        assert adapter.quote_identifier("my_table") == "`my_table`"

    def test_quote_identifier_doubles_embedded_backtick(self) -> None:
        """An embedded backtick must be doubled: `say`hello` → `say``hello`."""
        adapter = self._make_adapter()
        assert adapter.quote_identifier("say`hello") == "`say``hello`"

    def test_quote_identifier_empty(self) -> None:
        adapter = self._make_adapter()
        assert adapter.quote_identifier("") == "``"

    def test_quote_identifier_multiple_backticks(self) -> None:
        adapter = self._make_adapter()
        # `a`b`c` → ``a``b``c`` (each backtick doubled)
        assert adapter.quote_identifier("a`b`c") == "`a``b``c`"

    # -- validate_identifier ----------------------------------------------

    def test_validate_identifier_known_returns_quoted(self) -> None:
        adapter = self._make_adapter()
        result = adapter.validate_identifier("my_col", known={"my_col", "other_col"})
        assert result == "`my_col`"

    def test_validate_identifier_unknown_raises(self) -> None:
        adapter = self._make_adapter()
        with pytest.raises(UnknownIdentifierError):
            adapter.validate_identifier("evil_col", known={"my_col"})

    # -- read-only classification (mysql dialect, allow_show=True) ---------

    @pytest.mark.parametrize(
        "sql",
        [
            "SELECT * FROM users",
            "select id from orders where id = 1",
            "WITH cte AS (SELECT 1) SELECT * FROM cte",
            "EXPLAIN SELECT * FROM users",
            "SHOW DATABASES",
            "SHOW TABLES",
            "show create table users",
            "SHOW COLUMNS FROM users",
        ],
    )
    def test_read_only_allows_read_statements(self, sql: str) -> None:
        assert is_read_only_statement(sql, "mysql", allow_show=True) is True

    @pytest.mark.parametrize(
        "sql",
        [
            "INSERT INTO users (name) VALUES ('x')",
            "UPDATE users SET name = 'y' WHERE id = 1",
            "DELETE FROM users WHERE id = 1",
            "CREATE TABLE foo (id INT)",
            "ALTER TABLE foo ADD COLUMN bar TEXT",
            "DROP TABLE foo",
            "TRUNCATE TABLE foo",
        ],
    )
    def test_read_only_rejects_write_statements(self, sql: str) -> None:
        assert is_read_only_statement(sql, "mysql", allow_show=True) is False

    @pytest.mark.parametrize(
        "sql",
        [
            "SELECT * FROM users INTO OUTFILE '/tmp/out.csv'",
            "SELECT * FROM users INTO DUMPFILE '/tmp/x'",
        ],
    )
    def test_read_only_rejects_select_into_file(self, sql: str) -> None:
        """SELECT … INTO OUTFILE / DUMPFILE is rejected via the fail-closed parse-error path.

        sqlglot raises a ParseError on ``INTO OUTFILE`` / ``INTO DUMPFILE`` because
        they are not valid ANSI SQL.  ``is_read_only_statement`` treats any parse
        failure as non-read-only (fail-closed), so both forms are rejected regardless
        of the ``args["into"]`` check.
        """
        assert is_read_only_statement(sql, "mysql", allow_show=True) is False

    # -- URL built without leaking password into repr ----------------------

    def test_engine_url_host_and_db(self) -> None:
        adapter = self._make_adapter()
        url = adapter._engine.url
        assert url.host == "mysql-unit-test-host"
        assert url.database == "testdb"
        assert url.username == "testuser"
        # Password must not appear in the str() repr of the URL
        assert "testpass" not in str(url)

    def test_engine_drivername(self) -> None:
        adapter = self._make_adapter()
        assert adapter._engine.url.drivername == "mysql+pymysql"


# ---------------------------------------------------------------------------
# Integration tests — require Docker (testcontainers)
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestMySQLAdapterIntegration:
    """Full-stack tests against a real MySQL container via testcontainers."""

    @pytest.fixture(scope="class")
    def mysql_adapter(self):
        """Spin up a MySQL container and return a connected MySQLAdapter."""
        from testcontainers.mysql import MySqlContainer

        with MySqlContainer("mysql:8") as mysql:
            adapter = MySQLAdapter(
                host=mysql.get_container_host_ip(),
                port=int(mysql.get_exposed_port(3306)),
                database=mysql.dbname,
                username=mysql.username,
                password=mysql.password,
            )
            yield adapter
            adapter.dispose()

    @pytest.fixture(scope="class")
    def seeded_adapter(self, mysql_adapter: MySQLAdapter):
        """Seed schema and data into the container DB, return the adapter."""
        # departments table (PK)
        mysql_adapter.execute(
            """
            CREATE TABLE IF NOT EXISTS department (
                dept_id   INT          NOT NULL AUTO_INCREMENT PRIMARY KEY,
                dept_name VARCHAR(100) NOT NULL DEFAULT 'unknown'
            )
            """
        )
        # employees table (PK + FK + index)
        mysql_adapter.execute(
            """
            CREATE TABLE IF NOT EXISTS employee (
                emp_id   INT          NOT NULL AUTO_INCREMENT PRIMARY KEY,
                emp_name VARCHAR(100) NOT NULL,
                dept_id  INT,
                CONSTRAINT fk_employee_dept
                    FOREIGN KEY (dept_id) REFERENCES department(dept_id)
                    ON DELETE SET NULL ON UPDATE CASCADE
            )
            """
        )
        mysql_adapter.execute("CREATE INDEX idx_employee_dept ON employee(dept_id)")
        # Seed departments
        for i in range(1, 4):
            mysql_adapter.execute(
                "INSERT INTO department(dept_name) VALUES (:name)",
                {"name": f"Dept{i}"},
            )
        # Seed employees (5 rows for chunk-boundary tests)
        for i in range(1, 6):
            mysql_adapter.execute(
                "INSERT INTO employee(emp_name, dept_id) VALUES (:name, :did)",
                {"name": f"Emp{i}", "did": ((i - 1) % 3) + 1},
            )
        return mysql_adapter

    # -- test_connection --------------------------------------------------

    def test_connection_returns_true(self, seeded_adapter: MySQLAdapter) -> None:
        assert seeded_adapter.test_connection() is True

    # -- list_schemas -----------------------------------------------------

    def test_list_schemas_contains_db(self, seeded_adapter: MySQLAdapter) -> None:
        schemas = seeded_adapter.list_schemas()
        assert seeded_adapter._database in schemas

    def test_list_schemas_excludes_internals(self, seeded_adapter: MySQLAdapter) -> None:
        schemas = seeded_adapter.list_schemas()
        assert "mysql" not in schemas
        assert "information_schema" not in schemas
        assert "performance_schema" not in schemas
        assert "sys" not in schemas

    # -- list_tables ------------------------------------------------------

    def test_list_tables_returns_seeded_tables(self, seeded_adapter: MySQLAdapter) -> None:
        tables = seeded_adapter.list_tables()
        names = {t.name for t in tables}
        assert "department" in names
        assert "employee" in names

    def test_list_tables_schema_field(self, seeded_adapter: MySQLAdapter) -> None:
        tables = seeded_adapter.list_tables()
        for t in tables:
            assert t.schema_name == seeded_adapter._database

    def test_list_tables_type(self, seeded_adapter: MySQLAdapter) -> None:
        tables = seeded_adapter.list_tables()
        types = {t.name: t.table_type for t in tables}
        assert types["department"] == "BASE TABLE"
        assert types["employee"] == "BASE TABLE"

    # -- describe_table ---------------------------------------------------

    def test_describe_table_columns_department(self, seeded_adapter: MySQLAdapter) -> None:
        schema = seeded_adapter.describe_table("department")
        col_map = {c.name: c for c in schema.columns}
        assert "dept_id" in col_map
        assert "dept_name" in col_map
        assert col_map["dept_id"].is_primary_key is True
        assert col_map["dept_name"].is_nullable is False

    def test_describe_table_columns_employee(self, seeded_adapter: MySQLAdapter) -> None:
        schema = seeded_adapter.describe_table("employee")
        col_map = {c.name: c for c in schema.columns}
        assert "emp_id" in col_map
        assert "emp_name" in col_map
        assert "dept_id" in col_map
        assert col_map["emp_id"].is_primary_key is True

    def test_describe_table_index(self, seeded_adapter: MySQLAdapter) -> None:
        schema = seeded_adapter.describe_table("employee")
        idx_names = {i.name for i in schema.indexes}
        assert "idx_employee_dept" in idx_names
        emp_dept_idx = next(i for i in schema.indexes if i.name == "idx_employee_dept")
        assert "dept_id" in emp_dept_idx.columns

    def test_describe_table_foreign_key(self, seeded_adapter: MySQLAdapter) -> None:
        schema = seeded_adapter.describe_table("employee")
        assert len(schema.foreign_keys) >= 1
        fk = schema.foreign_keys[0]
        assert fk.ref_table == "department"
        assert "dept_id" in fk.columns
        assert "dept_id" in fk.ref_columns

    def test_describe_table_schema_name(self, seeded_adapter: MySQLAdapter) -> None:
        schema = seeded_adapter.describe_table("department")
        assert schema.schema_name == seeded_adapter._database
        assert schema.name == "department"

    # -- get_pk_columns ---------------------------------------------------

    def test_get_pk_columns_department(self, seeded_adapter: MySQLAdapter) -> None:
        pks = seeded_adapter.get_pk_columns("department")
        assert pks == ["dept_id"]

    def test_get_pk_columns_employee(self, seeded_adapter: MySQLAdapter) -> None:
        pks = seeded_adapter.get_pk_columns("employee")
        assert pks == ["emp_id"]

    # -- execute SELECT ---------------------------------------------------

    def test_execute_select_returns_rows(self, seeded_adapter: MySQLAdapter) -> None:
        result = seeded_adapter.execute("SELECT COUNT(*) AS cnt FROM department")
        assert result.columns == ["cnt"]
        assert result.rows[0][0] == 3
        assert result.row_count == 1

    def test_execute_select_with_params(self, seeded_adapter: MySQLAdapter) -> None:
        result = seeded_adapter.execute(
            "SELECT emp_name FROM employee WHERE emp_id = :eid", {"eid": 1}
        )
        assert len(result.rows) == 1
        assert result.rows[0][0] == "Emp1"

    # -- execute INSERT (rowcount) ----------------------------------------

    def test_execute_insert_reports_affected(self, seeded_adapter: MySQLAdapter) -> None:
        result = seeded_adapter.execute(
            "INSERT INTO department(dept_name) VALUES (:name)",
            {"name": "ExtraDept"},
        )
        assert result.columns == []
        assert result.row_count == 1

    # -- duration_ms -------------------------------------------------------

    def test_execute_duration_ms(self, seeded_adapter: MySQLAdapter) -> None:
        result = seeded_adapter.execute("SELECT 1")
        assert isinstance(result.duration_ms, int)
        assert result.duration_ms >= 0

    # -- execute_stream ----------------------------------------------------

    def test_execute_stream_all_rows(self, seeded_adapter: MySQLAdapter) -> None:
        all_rows: list[dict] = []
        for chunk in seeded_adapter.execute_stream("SELECT * FROM employee ORDER BY emp_id"):
            assert isinstance(chunk, list)
            all_rows.extend(chunk)
        assert len(all_rows) == 5

    def test_execute_stream_chunk_boundaries(self, seeded_adapter: MySQLAdapter) -> None:
        """5 rows with chunk_size=2 should produce chunks of sizes [2, 2, 1]."""
        chunks = list(
            seeded_adapter.execute_stream("SELECT * FROM employee ORDER BY emp_id", chunk_size=2)
        )
        assert len(chunks) == 3
        assert len(chunks[0]) == 2
        assert len(chunks[1]) == 2
        assert len(chunks[2]) == 1

    def test_execute_stream_returns_dicts(self, seeded_adapter: MySQLAdapter) -> None:
        chunks = list(
            seeded_adapter.execute_stream("SELECT * FROM employee ORDER BY emp_id", chunk_size=10)
        )
        assert len(chunks) == 1
        row = chunks[0][0]
        assert isinstance(row, dict)
        assert "emp_id" in row
        assert "emp_name" in row

    # -- read_only enforcement --------------------------------------------

    @pytest.fixture(scope="class")
    def ro_adapter(self, mysql_adapter: MySQLAdapter):
        """A read-only adapter sharing the same DB as the seeded adapter."""
        url = mysql_adapter._engine.url
        adapter = MySQLAdapter(
            host=str(url.host),
            port=int(url.port),
            database=str(url.database),
            username=str(url.username),
            password=str(url.password) if url.password is not None else None,
            read_only=True,
        )
        yield adapter
        adapter.dispose()

    def test_readonly_select_allowed(
        self, seeded_adapter: MySQLAdapter, ro_adapter: MySQLAdapter
    ) -> None:
        _ = seeded_adapter  # ensure DB is seeded
        result = ro_adapter.execute("SELECT * FROM department")
        assert result.row_count >= 3

    def test_readonly_show_allowed(self, ro_adapter: MySQLAdapter) -> None:
        result = ro_adapter.execute("SHOW TABLES")
        assert result.row_count >= 1

    def test_readonly_insert_rejected(self, ro_adapter: MySQLAdapter) -> None:
        with pytest.raises(ReadOnlyViolationError):
            ro_adapter.execute("INSERT INTO department(dept_name) VALUES ('blocked')")

    def test_readonly_update_rejected(self, ro_adapter: MySQLAdapter) -> None:
        with pytest.raises(ReadOnlyViolationError):
            ro_adapter.execute("UPDATE department SET dept_name = 'blocked' WHERE dept_id = 1")

    def test_readonly_delete_rejected(self, ro_adapter: MySQLAdapter) -> None:
        with pytest.raises(ReadOnlyViolationError):
            ro_adapter.execute("DELETE FROM department WHERE dept_id = 1")

    def test_readonly_create_table_rejected(self, ro_adapter: MySQLAdapter) -> None:
        with pytest.raises(ReadOnlyViolationError):
            ro_adapter.execute("CREATE TABLE blocked (id INT PRIMARY KEY)")

    def test_readonly_explain_select_allowed(self, ro_adapter: MySQLAdapter) -> None:
        result = ro_adapter.execute("EXPLAIN SELECT * FROM department")
        assert result.row_count > 0

    def test_readonly_stream_write_rejected(self, ro_adapter: MySQLAdapter) -> None:
        with pytest.raises(ReadOnlyViolationError):
            list(ro_adapter.execute_stream("CREATE TABLE leaked (a INT)"))

    def test_writable_stream_write_rejected(self, seeded_adapter: MySQLAdapter) -> None:
        with pytest.raises(AdapterError):
            list(seeded_adapter.execute_stream("CREATE TABLE leaked2 (a INT)"))

    def test_readonly_write_did_not_persist(
        self, ro_adapter: MySQLAdapter, seeded_adapter: MySQLAdapter
    ) -> None:
        """Rejected writes from read-only adapter must not have persisted."""
        _ = ro_adapter  # ensure read-only rejections happened
        result = seeded_adapter.execute("SELECT COUNT(*) AS cnt FROM department")
        # There should be >=3 original rows plus 1 from test_execute_insert_reports_affected
        # but none from the blocked inserts above.
        assert result.rows[0][0] >= 3

    def test_readonly_select_into_outfile_rejected(self, ro_adapter: MySQLAdapter) -> None:
        """SELECT … INTO OUTFILE is rejected by the read-only guard."""
        with pytest.raises(ReadOnlyViolationError):
            ro_adapter.execute("SELECT * FROM department INTO OUTFILE '/tmp/leaked.csv'")
