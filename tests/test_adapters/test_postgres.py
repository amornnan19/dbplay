"""Tests for PostgresAdapter — unit tests (no Docker) and integration tests (testcontainers)."""

import pytest

from pydbplay.adapters.base import AdapterError, ReadOnlyViolationError, UnknownIdentifierError
from pydbplay.adapters.postgres import PostgresAdapter
from pydbplay.core.sql_validator import is_read_only_statement

# ---------------------------------------------------------------------------
# Unit tests — no DB connection required (engine is lazy-connected)
# ---------------------------------------------------------------------------


class TestPostgresAdapterUnit:
    """Dialect, identifier, and read-only classification — no DB needed."""

    def _make_adapter(self, *, read_only: bool = False) -> PostgresAdapter:
        """Build a PostgresAdapter pointing at a non-existent host (lazy engine)."""
        return PostgresAdapter(
            host="pg-unit-test-host",
            port=5432,
            database="testdb",
            username="testuser",
            password="testpass",
            read_only=read_only,
        )

    # -- dialect ----------------------------------------------------------

    def test_dialect(self) -> None:
        adapter = self._make_adapter()
        assert adapter.dialect == "postgres"

    # -- quote_identifier -------------------------------------------------

    def test_quote_identifier_basic(self) -> None:
        adapter = self._make_adapter()
        assert adapter.quote_identifier("my_table") == '"my_table"'

    def test_quote_identifier_doubles_embedded_quote(self) -> None:
        adapter = self._make_adapter()
        assert adapter.quote_identifier('say"hello') == '"say""hello"'

    def test_quote_identifier_empty(self) -> None:
        adapter = self._make_adapter()
        assert adapter.quote_identifier("") == '""'

    # -- validate_identifier ----------------------------------------------

    def test_validate_identifier_known_returns_quoted(self) -> None:
        adapter = self._make_adapter()
        result = adapter.validate_identifier("my_col", known={"my_col", "other_col"})
        assert result == '"my_col"'

    def test_validate_identifier_unknown_raises(self) -> None:
        adapter = self._make_adapter()
        with pytest.raises(UnknownIdentifierError):
            adapter.validate_identifier("evil_col", known={"my_col"})

    # -- read-only classification (postgres dialect) ----------------------

    @pytest.mark.parametrize(
        "sql",
        [
            "SELECT * FROM users",
            "select id from orders where id = 1",
            "WITH cte AS (SELECT 1) SELECT * FROM cte",
            "EXPLAIN SELECT * FROM users",
            "EXPLAIN (ANALYZE) SELECT * FROM users",
            "SHOW search_path",
            "show TimeZone",
        ],
    )
    def test_read_only_allows_read_statements(self, sql: str) -> None:
        assert is_read_only_statement(sql, "postgres", allow_show=True) is True

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
        assert is_read_only_statement(sql, "postgres", allow_show=True) is False

    # -- URL built without leaking password into repr ----------------------

    def test_engine_url_host_and_db(self) -> None:
        adapter = self._make_adapter()
        url = adapter._engine.url
        assert url.host == "pg-unit-test-host"
        assert url.database == "testdb"
        assert url.username == "testuser"
        # Password must not appear in the str() repr of the URL
        assert "testpass" not in str(url)

    def test_engine_drivername(self) -> None:
        adapter = self._make_adapter()
        assert adapter._engine.url.drivername == "postgresql+psycopg"


# ---------------------------------------------------------------------------
# Integration tests — require Docker (testcontainers)
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestPostgresAdapterIntegration:
    """Full-stack tests against a real Postgres container via testcontainers."""

    @pytest.fixture(scope="class")
    def pg_adapter(self):
        """Spin up a Postgres container and return a connected PostgresAdapter."""
        from testcontainers.postgres import PostgresContainer

        with PostgresContainer("postgres:16-alpine") as pg:
            adapter = PostgresAdapter(
                host=pg.get_container_host_ip(),
                port=int(pg.get_exposed_port(5432)),
                database=pg.dbname,
                username=pg.username,
                password=pg.password,
            )
            yield adapter
            adapter.dispose()

    @pytest.fixture(scope="class")
    def seeded_adapter(self, pg_adapter: PostgresAdapter):
        """Seed schema and data into the container DB, return the adapter."""
        # departments table (PK)
        pg_adapter.execute(
            """
            CREATE TABLE IF NOT EXISTS department (
                dept_id   SERIAL PRIMARY KEY,
                dept_name TEXT NOT NULL DEFAULT 'unknown'
            )
            """
        )
        # employees table (PK + FK + index)
        pg_adapter.execute(
            """
            CREATE TABLE IF NOT EXISTS employee (
                emp_id   SERIAL PRIMARY KEY,
                emp_name TEXT    NOT NULL,
                dept_id  INTEGER REFERENCES department(dept_id)
                                 ON DELETE SET NULL ON UPDATE CASCADE
            )
            """
        )
        pg_adapter.execute("CREATE INDEX IF NOT EXISTS idx_employee_dept ON employee(dept_id)")
        # Seed departments
        for i in range(1, 4):
            pg_adapter.execute(
                "INSERT INTO department(dept_name) VALUES (:name)",
                {"name": f"Dept{i}"},
            )
        # Seed employees (5 rows for chunk-boundary tests)
        for i in range(1, 6):
            pg_adapter.execute(
                "INSERT INTO employee(emp_name, dept_id) VALUES (:name, :did)",
                {"name": f"Emp{i}", "did": ((i - 1) % 3) + 1},
            )
        return pg_adapter

    # -- test_connection --------------------------------------------------

    def test_connection_returns_true(self, seeded_adapter: PostgresAdapter) -> None:
        assert seeded_adapter.test_connection() is True

    # -- list_schemas -----------------------------------------------------

    def test_list_schemas_contains_public(self, seeded_adapter: PostgresAdapter) -> None:
        schemas = seeded_adapter.list_schemas()
        assert "public" in schemas

    def test_list_schemas_excludes_pg_catalog(self, seeded_adapter: PostgresAdapter) -> None:
        schemas = seeded_adapter.list_schemas()
        assert "pg_catalog" not in schemas
        assert "information_schema" not in schemas

    # -- list_tables ------------------------------------------------------

    def test_list_tables_returns_seeded_tables(self, seeded_adapter: PostgresAdapter) -> None:
        tables = seeded_adapter.list_tables()
        names = {t.name for t in tables}
        assert "department" in names
        assert "employee" in names

    def test_list_tables_schema_field(self, seeded_adapter: PostgresAdapter) -> None:
        tables = seeded_adapter.list_tables()
        for t in tables:
            assert t.schema_name == "public"

    def test_list_tables_type(self, seeded_adapter: PostgresAdapter) -> None:
        tables = seeded_adapter.list_tables()
        types = {t.name: t.table_type for t in tables}
        assert types["department"] == "BASE TABLE"
        assert types["employee"] == "BASE TABLE"

    # -- describe_table ---------------------------------------------------

    def test_describe_table_columns_department(self, seeded_adapter: PostgresAdapter) -> None:
        schema = seeded_adapter.describe_table("department")
        col_map = {c.name: c for c in schema.columns}
        assert "dept_id" in col_map
        assert "dept_name" in col_map
        assert col_map["dept_id"].is_primary_key is True
        assert col_map["dept_name"].is_nullable is False

    def test_describe_table_columns_employee(self, seeded_adapter: PostgresAdapter) -> None:
        schema = seeded_adapter.describe_table("employee")
        col_map = {c.name: c for c in schema.columns}
        assert "emp_id" in col_map
        assert "emp_name" in col_map
        assert "dept_id" in col_map
        assert col_map["emp_id"].is_primary_key is True

    def test_describe_table_index(self, seeded_adapter: PostgresAdapter) -> None:
        schema = seeded_adapter.describe_table("employee")
        idx_names = {i.name for i in schema.indexes}
        assert "idx_employee_dept" in idx_names
        emp_dept_idx = next(i for i in schema.indexes if i.name == "idx_employee_dept")
        assert "dept_id" in emp_dept_idx.columns

    def test_describe_table_foreign_key(self, seeded_adapter: PostgresAdapter) -> None:
        schema = seeded_adapter.describe_table("employee")
        assert len(schema.foreign_keys) >= 1
        fk = schema.foreign_keys[0]
        assert fk.ref_table == "department"
        assert "dept_id" in fk.columns
        assert "dept_id" in fk.ref_columns

    def test_describe_table_schema_name(self, seeded_adapter: PostgresAdapter) -> None:
        schema = seeded_adapter.describe_table("department")
        assert schema.schema_name == "public"
        assert schema.name == "department"

    # -- get_pk_columns ---------------------------------------------------

    def test_get_pk_columns_department(self, seeded_adapter: PostgresAdapter) -> None:
        pks = seeded_adapter.get_pk_columns("department")
        assert pks == ["dept_id"]

    def test_get_pk_columns_employee(self, seeded_adapter: PostgresAdapter) -> None:
        pks = seeded_adapter.get_pk_columns("employee")
        assert pks == ["emp_id"]

    # -- execute SELECT ---------------------------------------------------

    def test_execute_select_returns_rows(self, seeded_adapter: PostgresAdapter) -> None:
        result = seeded_adapter.execute("SELECT COUNT(*) AS cnt FROM department")
        assert result.columns == ["cnt"]
        assert result.rows[0][0] == 3
        assert result.row_count == 1

    def test_execute_select_with_params(self, seeded_adapter: PostgresAdapter) -> None:
        result = seeded_adapter.execute(
            "SELECT emp_name FROM employee WHERE emp_id = :eid", {"eid": 1}
        )
        assert len(result.rows) == 1
        assert result.rows[0][0] == "Emp1"

    # -- execute INSERT (rowcount) ----------------------------------------

    def test_execute_insert_reports_affected(self, seeded_adapter: PostgresAdapter) -> None:
        result = seeded_adapter.execute(
            "INSERT INTO department(dept_name) VALUES (:name)",
            {"name": "ExtraDept"},
        )
        assert result.columns == []
        assert result.row_count == 1

    # -- duration_ms -------------------------------------------------------

    def test_execute_duration_ms(self, seeded_adapter: PostgresAdapter) -> None:
        result = seeded_adapter.execute("SELECT 1")
        assert isinstance(result.duration_ms, int)
        assert result.duration_ms >= 0

    # -- execute_stream ----------------------------------------------------

    def test_execute_stream_all_rows(self, seeded_adapter: PostgresAdapter) -> None:
        all_rows: list[dict] = []
        for chunk in seeded_adapter.execute_stream("SELECT * FROM employee ORDER BY emp_id"):
            assert isinstance(chunk, list)
            all_rows.extend(chunk)
        assert len(all_rows) == 5

    def test_execute_stream_chunk_boundaries(self, seeded_adapter: PostgresAdapter) -> None:
        """5 rows with chunk_size=2 should produce chunks of sizes [2, 2, 1]."""
        chunks = list(
            seeded_adapter.execute_stream("SELECT * FROM employee ORDER BY emp_id", chunk_size=2)
        )
        assert len(chunks) == 3
        assert len(chunks[0]) == 2
        assert len(chunks[1]) == 2
        assert len(chunks[2]) == 1

    def test_execute_stream_returns_dicts(self, seeded_adapter: PostgresAdapter) -> None:
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
    def ro_adapter(self, pg_adapter: PostgresAdapter):
        """A read-only adapter sharing the same DB as the seeded adapter."""
        # Re-use the already-running container URL from the main adapter.
        # url.password is the plaintext password string (not masked) on SQLAlchemy URL.
        url = pg_adapter._engine.url
        adapter = PostgresAdapter(
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
        self, seeded_adapter: PostgresAdapter, ro_adapter: PostgresAdapter
    ) -> None:
        # Ensure seeded data exists before read
        _ = seeded_adapter
        result = ro_adapter.execute("SELECT * FROM department")
        assert result.row_count >= 3

    def test_readonly_insert_rejected(self, ro_adapter: PostgresAdapter) -> None:
        with pytest.raises(ReadOnlyViolationError):
            ro_adapter.execute("INSERT INTO department(dept_name) VALUES ('blocked')")

    def test_readonly_update_rejected(self, ro_adapter: PostgresAdapter) -> None:
        with pytest.raises(ReadOnlyViolationError):
            ro_adapter.execute("UPDATE department SET dept_name = 'blocked' WHERE dept_id = 1")

    def test_readonly_delete_rejected(self, ro_adapter: PostgresAdapter) -> None:
        with pytest.raises(ReadOnlyViolationError):
            ro_adapter.execute("DELETE FROM department WHERE dept_id = 1")

    def test_readonly_create_table_rejected(self, ro_adapter: PostgresAdapter) -> None:
        with pytest.raises(ReadOnlyViolationError):
            ro_adapter.execute("CREATE TABLE blocked (id INT PRIMARY KEY)")

    def test_readonly_alter_table_rejected(self, ro_adapter: PostgresAdapter) -> None:
        with pytest.raises(ReadOnlyViolationError):
            ro_adapter.execute("ALTER TABLE department ADD COLUMN extra TEXT")

    def test_readonly_explain_select_allowed(self, ro_adapter: PostgresAdapter) -> None:
        result = ro_adapter.execute("EXPLAIN SELECT * FROM department")
        assert result.row_count > 0

    def test_readonly_stream_write_rejected(self, ro_adapter: PostgresAdapter) -> None:
        with pytest.raises(ReadOnlyViolationError):
            list(ro_adapter.execute_stream("CREATE TABLE leaked (a INT)"))

    def test_writable_stream_write_rejected(self, seeded_adapter: PostgresAdapter) -> None:
        with pytest.raises(AdapterError):
            list(seeded_adapter.execute_stream("CREATE TABLE leaked2 (a INT)"))

    # -- read-only bypass cases (critical regression tests) ------------------

    def test_readonly_select_into_rejected(
        self, ro_adapter: PostgresAdapter, seeded_adapter: PostgresAdapter
    ) -> None:
        """SELECT 1 INTO leaked on a read-only adapter must raise ReadOnlyViolationError
        and must NOT create the table."""
        _ = seeded_adapter  # ensure DB is seeded
        with pytest.raises(ReadOnlyViolationError):
            ro_adapter.execute("SELECT 1 INTO leaked")

        # Verify table was NOT created by checking via a writable adapter
        tables = {t.name for t in seeded_adapter.list_tables()}
        assert "leaked" not in tables

    def test_readonly_cte_insert_rejected(
        self, ro_adapter: PostgresAdapter, seeded_adapter: PostgresAdapter
    ) -> None:
        """WITH x AS (INSERT … RETURNING *) SELECT must raise ReadOnlyViolationError
        and must NOT insert any row."""
        _ = seeded_adapter
        before = seeded_adapter.execute("SELECT COUNT(*) AS cnt FROM department")
        before_count = before.rows[0][0]

        with pytest.raises(ReadOnlyViolationError):
            ro_adapter.execute(
                "WITH x AS (INSERT INTO department(dept_name) VALUES ('bypassed') RETURNING *)"
                " SELECT * FROM x"
            )

        after = seeded_adapter.execute("SELECT COUNT(*) AS cnt FROM department")
        assert after.rows[0][0] == before_count

    def test_readonly_cte_update_rejected(
        self, ro_adapter: PostgresAdapter, seeded_adapter: PostgresAdapter
    ) -> None:
        """WITH x AS (UPDATE … RETURNING *) SELECT must raise ReadOnlyViolationError
        and must NOT modify any row."""
        _ = seeded_adapter
        original = seeded_adapter.execute("SELECT dept_name FROM department WHERE dept_id = 1")
        original_name = original.rows[0][0]

        with pytest.raises(ReadOnlyViolationError):
            ro_adapter.execute(
                "WITH x AS (UPDATE department SET dept_name = 'bypassed'"
                " WHERE dept_id = 1 RETURNING *) SELECT * FROM x"
            )

        check = seeded_adapter.execute("SELECT dept_name FROM department WHERE dept_id = 1")
        assert check.rows[0][0] == original_name

    def test_readonly_cte_delete_rejected(
        self, ro_adapter: PostgresAdapter, seeded_adapter: PostgresAdapter
    ) -> None:
        """WITH x AS (DELETE … RETURNING *) SELECT must raise ReadOnlyViolationError
        and must NOT delete any row."""
        _ = seeded_adapter
        before = seeded_adapter.execute("SELECT COUNT(*) AS cnt FROM department")
        before_count = before.rows[0][0]

        with pytest.raises(ReadOnlyViolationError):
            ro_adapter.execute(
                "WITH x AS (DELETE FROM department WHERE dept_id = 999 RETURNING *) SELECT * FROM x"
            )

        after = seeded_adapter.execute("SELECT COUNT(*) AS cnt FROM department")
        assert after.rows[0][0] == before_count


# ---------------------------------------------------------------------------
# is_read_only_statement — shared classifier unit tests
# ---------------------------------------------------------------------------


class TestIsReadOnlyStatement:
    """Cover both the sqlite-pragma path and the postgres allow_show path."""

    # -- postgres allow_show path -----------------------------------------

    def test_postgres_show_allowed(self) -> None:
        assert is_read_only_statement("SHOW search_path", "postgres", allow_show=True) is True

    def test_postgres_show_not_allowed_when_flag_false(self) -> None:
        assert is_read_only_statement("SHOW search_path", "postgres", allow_show=False) is False

    def test_postgres_select_allowed(self) -> None:
        assert is_read_only_statement("SELECT 1", "postgres", allow_show=True) is True

    def test_postgres_explain_select_allowed(self) -> None:
        assert (
            is_read_only_statement("EXPLAIN SELECT * FROM t", "postgres", allow_show=True) is True
        )

    def test_postgres_insert_rejected(self) -> None:
        assert (
            is_read_only_statement("INSERT INTO t VALUES (1)", "postgres", allow_show=True) is False
        )

    # -- sqlite pragma path -----------------------------------------------

    def test_sqlite_read_pragma_allowed(self) -> None:
        pragmas = frozenset({"table_info", "index_list", "database_list"})
        assert (
            is_read_only_statement("PRAGMA table_info(foo)", "sqlite", extra_read_pragmas=pragmas)
            is True
        )

    def test_sqlite_unknown_pragma_rejected(self) -> None:
        pragmas = frozenset({"table_info"})
        assert (
            is_read_only_statement("PRAGMA user_version", "sqlite", extra_read_pragmas=pragmas)
            is False
        )

    def test_sqlite_write_pragma_rejected(self) -> None:
        pragmas = frozenset({"user_version"})
        # Even if name is in allowlist, assignment form is always rejected
        assert (
            is_read_only_statement("PRAGMA user_version = 1", "sqlite", extra_read_pragmas=pragmas)
            is False
        )

    def test_sqlite_explain_select_allowed(self) -> None:
        assert is_read_only_statement("EXPLAIN SELECT 1", "sqlite") is True

    def test_cte_select_allowed(self) -> None:
        sql = "WITH cte AS (SELECT 1 AS x) SELECT * FROM cte"
        assert is_read_only_statement(sql, "postgres", allow_show=True) is True

    # -- read-only bypass cases (critical: these must be REJECTED) -----------

    def test_select_into_rejected(self) -> None:
        """SELECT … INTO creates a table — must be rejected even though it starts with SELECT."""
        assert is_read_only_statement("SELECT 1 INTO leaked", "postgres", allow_show=True) is False

    def test_cte_insert_returning_rejected(self) -> None:
        """WITH x AS (INSERT … RETURNING *) SELECT … must be rejected."""
        sql = "WITH x AS (INSERT INTO t (col) VALUES ('v') RETURNING *) SELECT * FROM x"
        assert is_read_only_statement(sql, "postgres", allow_show=True) is False

    def test_cte_update_returning_rejected(self) -> None:
        """WITH x AS (UPDATE … RETURNING *) SELECT … must be rejected."""
        sql = "WITH x AS (UPDATE t SET col = 'v' WHERE id = 1 RETURNING *) SELECT * FROM x"
        assert is_read_only_statement(sql, "postgres", allow_show=True) is False

    def test_cte_delete_returning_rejected(self) -> None:
        """WITH x AS (DELETE … RETURNING *) SELECT … must be rejected."""
        sql = "WITH x AS (DELETE FROM t WHERE id = 1 RETURNING *) SELECT * FROM x"
        assert is_read_only_statement(sql, "postgres", allow_show=True) is False

    # -- legitimate reads that must NOT be affected by the new check ---------

    def test_select_for_update_allowed(self) -> None:
        """SELECT … FOR UPDATE has no INTO and no DML node — must remain allowed."""
        assert (
            is_read_only_statement(
                "SELECT * FROM t WHERE id = 1 FOR UPDATE", "postgres", allow_show=True
            )
            is True
        )

    def test_subquery_select_allowed(self) -> None:
        """SELECT with a subquery in WHERE must remain allowed."""
        sql = "SELECT * FROM t WHERE id IN (SELECT id FROM other WHERE flag = true)"
        assert is_read_only_statement(sql, "postgres", allow_show=True) is True

    def test_plain_cte_select_still_allowed(self) -> None:
        """WITH cte AS (SELECT …) SELECT … must still be allowed after the fix."""
        sql = "WITH summary AS (SELECT dept_id, COUNT(*) AS cnt FROM employee GROUP BY dept_id) SELECT * FROM summary"
        assert is_read_only_statement(sql, "postgres", allow_show=True) is True
