"""Tests for RowEditor — real SQLite target db, no Docker required."""

import sqlite3
from pathlib import Path

import pytest

from pydbplay.adapters.base import ReadOnlyViolationError, UnknownIdentifierError
from pydbplay.core.connection_manager import ConnectionManager
from pydbplay.core.row_editor import RowEditError, RowEditor
from pydbplay.db.repository import Repository, make_engine, run_migrations
from pydbplay.schemas.connection import ConnectionCreate

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SEED_NAMES = [
    "Alice",
    "Bob",
    "Carol",
    "Dave",
    "Eve",
    "Frank",
    "Grace",
    "Hank",
    "Iris",
    "Jack",
]


def _make_repo(tmp_path: Path) -> Repository:
    """Create a migrated app Repository backed by a tmp SQLite file.

    *tmp_path* is created (with parents) if it does not yet exist, so callers
    can pass subdirectories without pre-creating them.
    """
    tmp_path.mkdir(parents=True, exist_ok=True)
    db_path = tmp_path / "app.db"
    engine = make_engine(db_path)
    with engine.connect() as conn:
        run_migrations(conn)
    return Repository(engine)


def _seed_target_db(target_path: Path) -> None:
    """Create a users table with 10 rows and an orders table for FK coverage."""
    with sqlite3.connect(target_path) as cx:
        cx.execute(
            "CREATE TABLE users ("
            "  id   INTEGER PRIMARY KEY, "
            "  name TEXT    NOT NULL, "
            "  age  INTEGER NOT NULL"
            ")"
        )
        for idx, name in enumerate(_SEED_NAMES, start=1):
            cx.execute(
                "INSERT INTO users (id, name, age) VALUES (?, ?, ?)",
                (idx, name, 20 + idx),
            )

        # A second table referencing users — validates FK integrity is on
        cx.execute(
            "CREATE TABLE orders ("
            "  id      INTEGER PRIMARY KEY, "
            "  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE, "
            "  item    TEXT    NOT NULL"
            ")"
        )
        cx.execute("INSERT INTO orders (id, user_id, item) VALUES (1, 1, 'widget')")
        cx.commit()


def _register_profile(
    repo: Repository,
    db_path: Path,
    *,
    read_only: bool = False,
) -> int:
    profile = repo.create_connection(
        ConnectionCreate(
            name="Test SQLite",
            engine="sqlite",
            database=str(db_path),
            read_only=read_only,
        )
    )
    return profile.id


def _make_editor(
    tmp_path: Path,
    target_path: Path,
    *,
    read_only: bool = False,
) -> tuple[RowEditor, int, ConnectionManager]:
    """Return (editor, conn_id, cm) — caller must call cm.close_all() in finally."""
    repo = _make_repo(tmp_path)
    conn_id = _register_profile(repo, target_path, read_only=read_only)
    cm = ConnectionManager(repo)
    editor = RowEditor(cm)
    return editor, conn_id, cm


# ---------------------------------------------------------------------------
# browse — basic
# ---------------------------------------------------------------------------


def test_browse_returns_all_rows(tmp_path: Path) -> None:
    """browse() with page_size >= total returns all rows and has_next=False."""
    target = tmp_path / "target.db"
    _seed_target_db(target)
    editor, conn_id, cm = _make_editor(tmp_path, target)

    try:
        result = editor.browse(conn_id, "users", page_size=20)
        assert result.table == "users"
        assert "id" in result.columns
        assert "name" in result.columns
        assert "age" in result.columns
        assert len(result.rows) == 10
        assert result.has_next is False
        assert result.page == 1
        assert result.pk_columns == ["id"]
    finally:
        cm.close_all()


def test_browse_pk_columns(tmp_path: Path) -> None:
    """pk_columns must contain exactly ['id'] for the users table."""
    target = tmp_path / "target.db"
    _seed_target_db(target)
    editor, conn_id, cm = _make_editor(tmp_path, target)

    try:
        result = editor.browse(conn_id, "users")
        assert result.pk_columns == ["id"]
    finally:
        cm.close_all()


# ---------------------------------------------------------------------------
# browse — pagination
# ---------------------------------------------------------------------------


def test_browse_page_size_smaller_than_total_has_next_true(tmp_path: Path) -> None:
    """page_size < total → has_next=True on page 1."""
    target = tmp_path / "target.db"
    _seed_target_db(target)
    editor, conn_id, cm = _make_editor(tmp_path, target)

    try:
        result = editor.browse(conn_id, "users", page=1, page_size=4)
        assert len(result.rows) == 4
        assert result.has_next is True
    finally:
        cm.close_all()


def test_browse_last_page_has_next_false(tmp_path: Path) -> None:
    """Last page returns remaining rows and has_next=False."""
    target = tmp_path / "target.db"
    _seed_target_db(target)
    editor, conn_id, cm = _make_editor(tmp_path, target)

    try:
        # 10 rows, page_size=4 → pages 1,2 have 4 rows; page 3 has 2 rows
        result = editor.browse(conn_id, "users", page=3, page_size=4)
        assert len(result.rows) == 2
        assert result.has_next is False
    finally:
        cm.close_all()


def test_browse_page_2_returns_next_slice(tmp_path: Path) -> None:
    """Page 2 contains different rows from page 1."""
    target = tmp_path / "target.db"
    _seed_target_db(target)
    editor, conn_id, cm = _make_editor(tmp_path, target)

    try:
        page1 = editor.browse(conn_id, "users", page=1, page_size=5)
        page2 = editor.browse(conn_id, "users", page=2, page_size=5)
        ids_page1 = {row[page1.columns.index("id")] for row in page1.rows}
        ids_page2 = {row[page2.columns.index("id")] for row in page2.rows}
        assert ids_page1.isdisjoint(ids_page2), "Pages must not overlap"
        assert len(ids_page1) == 5
        assert len(ids_page2) == 5
    finally:
        cm.close_all()


# ---------------------------------------------------------------------------
# browse — filters
# ---------------------------------------------------------------------------


def test_browse_filter_equality_returns_matches(tmp_path: Path) -> None:
    """Filter on name='Alice' returns only the Alice row."""
    target = tmp_path / "target.db"
    _seed_target_db(target)
    editor, conn_id, cm = _make_editor(tmp_path, target)

    try:
        result = editor.browse(conn_id, "users", filters=[("name", "Alice")])
        assert len(result.rows) == 1
        name_idx = result.columns.index("name")
        assert result.rows[0][name_idx] == "Alice"
    finally:
        cm.close_all()


def test_browse_filter_no_matches(tmp_path: Path) -> None:
    """Filter for a name that does not exist returns zero rows."""
    target = tmp_path / "target.db"
    _seed_target_db(target)
    editor, conn_id, cm = _make_editor(tmp_path, target)

    try:
        result = editor.browse(conn_id, "users", filters=[("name", "Zed")])
        assert len(result.rows) == 0
        assert result.has_next is False
    finally:
        cm.close_all()


# ---------------------------------------------------------------------------
# browse — sort
# ---------------------------------------------------------------------------


def test_browse_sort_age_desc(tmp_path: Path) -> None:
    """sort=('age', 'DESC') returns rows in descending age order."""
    target = tmp_path / "target.db"
    _seed_target_db(target)
    editor, conn_id, cm = _make_editor(tmp_path, target)

    try:
        result = editor.browse(conn_id, "users", sort=("age", "DESC"), page_size=20)
        age_idx = result.columns.index("age")
        ages = [row[age_idx] for row in result.rows]
        assert ages == sorted(ages, reverse=True)
    finally:
        cm.close_all()


def test_browse_sort_age_asc(tmp_path: Path) -> None:
    """sort=('age', 'ASC') returns rows in ascending age order."""
    target = tmp_path / "target.db"
    _seed_target_db(target)
    editor, conn_id, cm = _make_editor(tmp_path, target)

    try:
        result = editor.browse(conn_id, "users", sort=("age", "ASC"), page_size=20)
        age_idx = result.columns.index("age")
        ages = [row[age_idx] for row in result.rows]
        assert ages == sorted(ages)
    finally:
        cm.close_all()


def test_browse_invalid_sort_direction_raises(tmp_path: Path) -> None:
    """An invalid sort direction raises RowEditError."""
    target = tmp_path / "target.db"
    _seed_target_db(target)
    editor, conn_id, cm = _make_editor(tmp_path, target)

    try:
        with pytest.raises(RowEditError, match="Invalid sort direction"):
            editor.browse(conn_id, "users", sort=("age", "SIDEWAYS"))
    finally:
        cm.close_all()


# ---------------------------------------------------------------------------
# browse — identifier safety
# ---------------------------------------------------------------------------


def test_browse_filter_nonexistent_column_raises(tmp_path: Path) -> None:
    """Filtering on a column not in the schema raises UnknownIdentifierError."""
    target = tmp_path / "target.db"
    _seed_target_db(target)
    editor, conn_id, cm = _make_editor(tmp_path, target)

    try:
        with pytest.raises(UnknownIdentifierError):
            editor.browse(conn_id, "users", filters=[("nonexistent_column", "x")])
    finally:
        cm.close_all()


def test_browse_sort_nonexistent_column_raises(tmp_path: Path) -> None:
    """Sorting on a column not in the schema raises UnknownIdentifierError."""
    target = tmp_path / "target.db"
    _seed_target_db(target)
    editor, conn_id, cm = _make_editor(tmp_path, target)

    try:
        with pytest.raises(UnknownIdentifierError):
            editor.browse(conn_id, "users", sort=("nonexistent_column", "ASC"))
    finally:
        cm.close_all()


def test_browse_malicious_column_rejected_table_intact(tmp_path: Path) -> None:
    """A SQL-injection attempt in a filter column is rejected; table survives."""
    target = tmp_path / "target.db"
    _seed_target_db(target)
    editor, conn_id, cm = _make_editor(tmp_path, target)

    try:
        with pytest.raises(UnknownIdentifierError):
            editor.browse(
                conn_id,
                "users",
                filters=[('id"; DROP TABLE users --', 1)],
            )
        # Table must still be intact
        result = editor.browse(conn_id, "users", page_size=20)
        assert len(result.rows) == 10
    finally:
        cm.close_all()


def test_browse_malicious_sort_column_rejected_table_intact(tmp_path: Path) -> None:
    """A SQL-injection attempt in the sort column is rejected; table survives."""
    target = tmp_path / "target.db"
    _seed_target_db(target)
    editor, conn_id, cm = _make_editor(tmp_path, target)

    try:
        with pytest.raises(UnknownIdentifierError):
            editor.browse(
                conn_id,
                "users",
                sort=('age"; DROP TABLE users --', "ASC"),
            )
        result = editor.browse(conn_id, "users", page_size=20)
        assert len(result.rows) == 10
    finally:
        cm.close_all()


# ---------------------------------------------------------------------------
# update_row
# ---------------------------------------------------------------------------


def test_update_row_changes_name(tmp_path: Path) -> None:
    """update_row changes the name field and the re-read value matches."""
    target = tmp_path / "target.db"
    _seed_target_db(target)
    editor, conn_id, cm = _make_editor(tmp_path, target)

    try:
        updated = editor.update_row(
            conn_id, "users", pk_values={"id": 1}, changes={"name": "Alicia"}
        )
        assert updated["name"] == "Alicia"

        # Confirm via browse
        result = editor.browse(conn_id, "users", filters=[("id", 1)])
        name_idx = result.columns.index("name")
        assert result.rows[0][name_idx] == "Alicia"
    finally:
        cm.close_all()


def test_update_row_unknown_column_raises(tmp_path: Path) -> None:
    """Changes with an unknown column name raises UnknownIdentifierError."""
    target = tmp_path / "target.db"
    _seed_target_db(target)
    editor, conn_id, cm = _make_editor(tmp_path, target)

    try:
        with pytest.raises(UnknownIdentifierError):
            editor.update_row(
                conn_id,
                "users",
                pk_values={"id": 1},
                changes={"nonexistent_col": "x"},
            )
    finally:
        cm.close_all()


def test_update_row_empty_changes_raises(tmp_path: Path) -> None:
    """Empty changes dict raises RowEditError."""
    target = tmp_path / "target.db"
    _seed_target_db(target)
    editor, conn_id, cm = _make_editor(tmp_path, target)

    try:
        with pytest.raises(RowEditError, match="changes"):
            editor.update_row(conn_id, "users", pk_values={"id": 1}, changes={})
    finally:
        cm.close_all()


def test_update_row_missing_pk_raises(tmp_path: Path) -> None:
    """Empty pk_values raises RowEditError."""
    target = tmp_path / "target.db"
    _seed_target_db(target)
    editor, conn_id, cm = _make_editor(tmp_path, target)

    try:
        with pytest.raises(RowEditError):
            editor.update_row(conn_id, "users", pk_values={}, changes={"name": "X"})
    finally:
        cm.close_all()


def test_update_row_partial_pk_raises(tmp_path: Path) -> None:
    """Supplying a partial PK (wrong column) raises RowEditError."""
    target = tmp_path / "target.db"
    _seed_target_db(target)
    editor, conn_id, cm = _make_editor(tmp_path, target)

    try:
        # users PK is {id}; supplying {name} is partial/wrong
        with pytest.raises(RowEditError):
            editor.update_row(conn_id, "users", pk_values={"name": "Alice"}, changes={"age": 99})
    finally:
        cm.close_all()


def test_update_row_read_only_raises(tmp_path: Path) -> None:
    """update_row on a read-only connection raises ReadOnlyViolationError."""
    target = tmp_path / "target.db"
    _seed_target_db(target)
    editor, conn_id, cm = _make_editor(tmp_path, target, read_only=True)

    try:
        with pytest.raises(ReadOnlyViolationError):
            editor.update_row(conn_id, "users", pk_values={"id": 1}, changes={"name": "HACKED"})

        # Row must be unchanged
        ro_result = editor.browse(conn_id, "users", filters=[("id", 1)])
        name_idx = ro_result.columns.index("name")
        assert ro_result.rows[0][name_idx] == "Alice"
    finally:
        cm.close_all()


def test_update_row_nonexistent_pk_returns_empty(tmp_path: Path) -> None:
    """update_row returns empty dict when no row matches the PK."""
    target = tmp_path / "target.db"
    _seed_target_db(target)
    editor, conn_id, cm = _make_editor(tmp_path, target)

    try:
        result = editor.update_row(
            conn_id, "users", pk_values={"id": 9999}, changes={"name": "Ghost"}
        )
        assert result == {}
    finally:
        cm.close_all()


# ---------------------------------------------------------------------------
# insert_row
# ---------------------------------------------------------------------------


def test_insert_row_visible_via_browse(tmp_path: Path) -> None:
    """insert_row inserts a row that is then visible via browse."""
    target = tmp_path / "target.db"
    _seed_target_db(target)
    editor, conn_id, cm = _make_editor(tmp_path, target)

    try:
        result = editor.insert_row(conn_id, "users", values={"id": 99, "name": "Zara", "age": 30})
        assert result["inserted"] is True

        browse = editor.browse(conn_id, "users", filters=[("id", 99)])
        assert len(browse.rows) == 1
        name_idx = browse.columns.index("name")
        assert browse.rows[0][name_idx] == "Zara"
    finally:
        cm.close_all()


def test_insert_row_unknown_column_raises(tmp_path: Path) -> None:
    """insert_row with an unknown column raises UnknownIdentifierError."""
    target = tmp_path / "target.db"
    _seed_target_db(target)
    editor, conn_id, cm = _make_editor(tmp_path, target)

    try:
        with pytest.raises(UnknownIdentifierError):
            editor.insert_row(conn_id, "users", values={"id": 100, "name": "X", "ghost_col": "y"})
    finally:
        cm.close_all()


def test_insert_row_empty_values_raises(tmp_path: Path) -> None:
    """insert_row with an empty values dict raises RowEditError."""
    target = tmp_path / "target.db"
    _seed_target_db(target)
    editor, conn_id, cm = _make_editor(tmp_path, target)

    try:
        with pytest.raises(RowEditError, match="values"):
            editor.insert_row(conn_id, "users", values={})
    finally:
        cm.close_all()


def test_insert_row_read_only_raises(tmp_path: Path) -> None:
    """insert_row on a read-only connection raises ReadOnlyViolationError."""
    target = tmp_path / "target.db"
    _seed_target_db(target)
    editor_rw, conn_id_rw, cm_rw = _make_editor(tmp_path, target, read_only=False)
    # Create a second repo/editor backed by the same DB file but read-only
    repo_ro = _make_repo(tmp_path / "ro_app")
    conn_id_ro = _register_profile(repo_ro, target, read_only=True)
    cm_ro = ConnectionManager(repo_ro)
    editor_ro = RowEditor(cm_ro)

    try:
        with pytest.raises(ReadOnlyViolationError):
            editor_ro.insert_row(
                conn_id_ro, "users", values={"id": 200, "name": "Hacker", "age": 1}
            )

        # Row must NOT be present
        browse = editor_rw.browse(conn_id_rw, "users", filters=[("id", 200)])
        assert len(browse.rows) == 0
    finally:
        cm_rw.close_all()
        cm_ro.close_all()


# ---------------------------------------------------------------------------
# delete_row
# ---------------------------------------------------------------------------


def test_delete_row_removes_row(tmp_path: Path) -> None:
    """delete_row returns True and the row is gone."""
    target = tmp_path / "target.db"
    _seed_target_db(target)
    editor, conn_id, cm = _make_editor(tmp_path, target)

    try:
        deleted = editor.delete_row(conn_id, "users", pk_values={"id": 5})
        assert deleted is True

        result = editor.browse(conn_id, "users", filters=[("id", 5)])
        assert len(result.rows) == 0
    finally:
        cm.close_all()


def test_delete_row_nonexistent_pk_returns_false(tmp_path: Path) -> None:
    """delete_row returns False when the PK does not exist."""
    target = tmp_path / "target.db"
    _seed_target_db(target)
    editor, conn_id, cm = _make_editor(tmp_path, target)

    try:
        result = editor.delete_row(conn_id, "users", pk_values={"id": 9999})
        assert result is False
    finally:
        cm.close_all()


def test_delete_row_read_only_raises_row_intact(tmp_path: Path) -> None:
    """delete_row on a read-only connection raises and the row is still present."""
    target = tmp_path / "target.db"
    _seed_target_db(target)
    editor_rw, conn_id_rw, cm_rw = _make_editor(tmp_path, target, read_only=False)
    repo_ro = _make_repo(tmp_path / "ro_app2")
    conn_id_ro = _register_profile(repo_ro, target, read_only=True)
    cm_ro = ConnectionManager(repo_ro)
    editor_ro = RowEditor(cm_ro)

    try:
        with pytest.raises(ReadOnlyViolationError):
            editor_ro.delete_row(conn_id_ro, "users", pk_values={"id": 3})

        result = editor_rw.browse(conn_id_rw, "users", filters=[("id", 3)])
        assert len(result.rows) == 1
    finally:
        cm_rw.close_all()
        cm_ro.close_all()


def test_delete_row_empty_pk_raises(tmp_path: Path) -> None:
    """delete_row with empty pk_values raises RowEditError."""
    target = tmp_path / "target.db"
    _seed_target_db(target)
    editor, conn_id, cm = _make_editor(tmp_path, target)

    try:
        with pytest.raises(RowEditError):
            editor.delete_row(conn_id, "users", pk_values={})
    finally:
        cm.close_all()


def test_delete_row_partial_pk_raises(tmp_path: Path) -> None:
    """delete_row with a wrong PK key raises RowEditError (no unqualified DELETE)."""
    target = tmp_path / "target.db"
    _seed_target_db(target)
    editor, conn_id, cm = _make_editor(tmp_path, target)

    try:
        # 'name' is not the PK for users
        with pytest.raises(RowEditError):
            editor.delete_row(conn_id, "users", pk_values={"name": "Alice"})
    finally:
        cm.close_all()


# ---------------------------------------------------------------------------
# update_row — PK change rejected (bug #1)
# ---------------------------------------------------------------------------


def test_update_row_rejects_changing_pk(tmp_path: Path) -> None:
    """Attempting to change a PK column raises RowEditError; row is unchanged."""
    target = tmp_path / "target.db"
    _seed_target_db(target)
    editor, conn_id, cm = _make_editor(tmp_path, target)

    try:
        with pytest.raises(RowEditError, match="cannot change primary key"):
            editor.update_row(
                conn_id,
                "users",
                pk_values={"id": 1},
                changes={"id": 999},
            )

        # Row with old PK must still exist and be unmodified
        result = editor.browse(conn_id, "users", filters=[("id", 1)])
        assert len(result.rows) == 1

        # Row with new PK must NOT exist
        result_new = editor.browse(conn_id, "users", filters=[("id", 999)])
        assert len(result_new.rows) == 0
    finally:
        cm.close_all()


# ---------------------------------------------------------------------------
# browse — page_size validation (bug #2)
# ---------------------------------------------------------------------------


def test_browse_page_size_zero_rejected(tmp_path: Path) -> None:
    """page_size=0 raises RowEditError and does not return any rows."""
    target = tmp_path / "target.db"
    _seed_target_db(target)
    editor, conn_id, cm = _make_editor(tmp_path, target)

    try:
        with pytest.raises(RowEditError, match="page_size must be >= 1"):
            editor.browse(conn_id, "users", page_size=0)
    finally:
        cm.close_all()


def test_browse_page_size_negative_rejected(tmp_path: Path) -> None:
    """page_size=-5 raises RowEditError and does NOT return the whole table."""
    target = tmp_path / "target.db"
    _seed_target_db(target)
    editor, conn_id, cm = _make_editor(tmp_path, target)

    try:
        with pytest.raises(RowEditError, match="page_size must be >= 1"):
            editor.browse(conn_id, "users", page_size=-5)
    finally:
        cm.close_all()


# ---------------------------------------------------------------------------
# update_row — no-op update returns row, not {} (bug #3 / cross-engine fix)
# ---------------------------------------------------------------------------


def test_update_row_noop_update_returns_row(tmp_path: Path) -> None:
    """Updating a column to its current value returns the row dict, not {}."""
    target = tmp_path / "target.db"
    _seed_target_db(target)
    editor, conn_id, cm = _make_editor(tmp_path, target)

    try:
        # Fetch Alice's current name and write it back unchanged
        browse = editor.browse(conn_id, "users", filters=[("id", 1)])
        name_idx = browse.columns.index("name")
        current_name = browse.rows[0][name_idx]

        result = editor.update_row(
            conn_id,
            "users",
            pk_values={"id": 1},
            changes={"name": current_name},  # same value — no-op
        )

        # Must return a non-empty dict (the unchanged row), never {}
        assert result != {}
        assert result["name"] == current_name
        assert result["id"] == 1
    finally:
        cm.close_all()


# ---------------------------------------------------------------------------
# update_row — composite-PK partial-key rejection (untested case)
# ---------------------------------------------------------------------------


def _seed_composite_pk_db(target_path: Path) -> None:
    """Create a table with a 2-column composite PK."""
    with sqlite3.connect(target_path) as cx:
        cx.execute(
            "CREATE TABLE memberships ("
            "  user_id  INTEGER NOT NULL, "
            "  group_id INTEGER NOT NULL, "
            "  role     TEXT    NOT NULL, "
            "  PRIMARY KEY (user_id, group_id)"
            ")"
        )
        cx.execute("INSERT INTO memberships VALUES (1, 10, 'admin')")
        cx.execute("INSERT INTO memberships VALUES (2, 10, 'member')")
        cx.commit()


def test_update_row_composite_pk_partial_key_rejected(tmp_path: Path) -> None:
    """Passing only one column of a 2-col PK raises RowEditError; nothing modified."""
    target = tmp_path / "target.db"
    _seed_composite_pk_db(target)
    editor, conn_id, cm = _make_editor(tmp_path, target)

    try:
        with pytest.raises(RowEditError):
            editor.update_row(
                conn_id,
                "memberships",
                pk_values={"user_id": 1},  # missing group_id
                changes={"role": "owner"},
            )

        # Row must be unchanged
        result = editor.browse(
            conn_id,
            "memberships",
            filters=[("user_id", 1), ("group_id", 10)],
        )
        role_idx = result.columns.index("role")
        assert result.rows[0][role_idx] == "admin"
    finally:
        cm.close_all()
