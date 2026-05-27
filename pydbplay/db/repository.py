"""SQLAlchemy Core Table definitions and Repository for app-internal SQLite.

Uses SQLAlchemy Core (Table + insert()/select()) — NOT the ORM.
Migration version tracked via SQLite PRAGMA user_version (no Alembic).
"""

# TODO(phase-1): Implement CRUD methods.

from pathlib import Path

import sqlalchemy as sa
from sqlalchemy import Connection, MetaData, Table, create_engine, text

# ---------------------------------------------------------------------------
# Metadata & Table definitions
# ---------------------------------------------------------------------------

metadata = MetaData()

connection_profile_table: Table = sa.Table(
    "connection_profile",
    metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("name", sa.Text, nullable=False),
    sa.Column("engine", sa.Text, nullable=False),
    sa.Column("host", sa.Text, nullable=True),
    sa.Column("port", sa.Integer, nullable=True),
    sa.Column("database", sa.Text, nullable=False),
    sa.Column("username", sa.Text, nullable=True),
    sa.Column("password_encrypted", sa.Text, nullable=True),
    sa.Column("ssl_mode", sa.Text, nullable=True),
    sa.Column("read_only", sa.Boolean, nullable=False, server_default="0"),
    sa.Column("color", sa.Text, nullable=True),
    sa.Column("created_at", sa.DateTime, nullable=False),
    sa.Column("updated_at", sa.DateTime, nullable=False),
    sa.Column("last_used_at", sa.DateTime, nullable=True),
)

query_history_table: Table = sa.Table(
    "query_history",
    metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("connection_id", sa.Integer, sa.ForeignKey("connection_profile.id"), nullable=False),
    sa.Column("sql", sa.Text, nullable=False),
    sa.Column("executed_at", sa.DateTime, nullable=False),
    sa.Column("duration_ms", sa.Integer, nullable=False),
    sa.Column("row_count", sa.Integer, nullable=True),
    sa.Column("success", sa.Boolean, nullable=False),
    sa.Column("error_message", sa.Text, nullable=True),
)

saved_query_table: Table = sa.Table(
    "saved_query",
    metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("connection_id", sa.Integer, sa.ForeignKey("connection_profile.id"), nullable=True),
    sa.Column("name", sa.Text, nullable=False),
    sa.Column("sql", sa.Text, nullable=False),
    sa.Column("description", sa.Text, nullable=True),
    sa.Column("created_at", sa.DateTime, nullable=False),
    sa.Column("updated_at", sa.DateTime, nullable=False),
)

# ---------------------------------------------------------------------------
# Migration runner
# ---------------------------------------------------------------------------

_MIGRATIONS_DIR = Path(__file__).parent / "migrations"

# Current schema version — bump when adding a new migration file
_CURRENT_VERSION = 1


def run_migrations(conn: Connection) -> None:
    """Apply pending SQL migrations tracked by PRAGMA user_version.

    Reads migration files from ``db/migrations/`` in numeric order and applies
    any whose sequence number exceeds the current ``PRAGMA user_version``.
    Each migration is applied in its own transaction; version is bumped after
    each individual migration so a later failure cannot leave tables created
    with ``user_version`` un-advanced.

    Args:
        conn: An open SQLAlchemy Core connection to the app SQLite database.
    """
    current: int = conn.execute(text("PRAGMA user_version")).scalar() or 0

    migration_files = sorted(_MIGRATIONS_DIR.glob("*.sql"))
    for migration_path in migration_files:
        # File names must be "NNN_description.sql" where NNN is the version number
        try:
            version = int(migration_path.stem.split("_")[0])
        except ValueError:
            continue

        if version <= current:
            continue

        sql_text = migration_path.read_text(encoding="utf-8")
        # SQLite's DBAPI only executes one statement at a time via execute();
        # use executescript() on the raw driver connection to handle multi-
        # statement migration files.
        raw = conn.connection.driver_connection  # sqlite3.Connection
        assert raw is not None, "Expected a live sqlite3 driver connection"
        raw.executescript(sql_text)
        conn.execute(text(f"PRAGMA user_version = {version}"))
        conn.commit()


# ---------------------------------------------------------------------------
# Repository
# ---------------------------------------------------------------------------


class Repository:
    """CRUD operations for app-internal SQLite via SQLAlchemy Core.

    All methods accept an open SQLAlchemy Core *Connection* so the caller
    controls transaction boundaries.
    """

    # ── ConnectionProfile ────────────────────────────────────────────────

    def list_connections(self, conn: Connection) -> list[dict]:
        """Return all connection profiles as plain dicts.

        Args:
            conn: Open SQLAlchemy Core connection.

        Returns:
            List of row dicts (column → value).
        """
        # TODO(phase-1): implement using Core select()
        raise NotImplementedError

    def get_connection(self, conn: Connection, connection_id: int) -> dict | None:
        """Fetch a single connection profile by ID.

        Args:
            conn: Open SQLAlchemy Core connection.
            connection_id: Primary key of the profile.

        Returns:
            Row dict or None if not found.
        """
        # TODO(phase-1): implement
        raise NotImplementedError

    def create_connection(self, conn: Connection, data: dict) -> int:
        """Insert a new connection profile and return the new ID.

        Args:
            conn: Open SQLAlchemy Core connection.
            data: Column → value mapping (excluding id).

        Returns:
            The auto-generated primary key.
        """
        # TODO(phase-1): implement using Core insert()
        raise NotImplementedError

    def update_connection(self, conn: Connection, connection_id: int, data: dict) -> None:
        """Update an existing connection profile.

        Args:
            conn: Open SQLAlchemy Core connection.
            connection_id: Profile to update.
            data: Partial column → value mapping with fields to update.
        """
        # TODO(phase-1): implement using Core update()
        raise NotImplementedError

    def delete_connection(self, conn: Connection, connection_id: int) -> None:
        """Delete a connection profile by ID.

        Args:
            conn: Open SQLAlchemy Core connection.
            connection_id: Profile to delete.
        """
        # TODO(phase-1): implement using Core delete()
        raise NotImplementedError

    # ── QueryHistory ─────────────────────────────────────────────────────

    def add_history(self, conn: Connection, data: dict) -> int:
        """Insert a query history record and return the new ID.

        Args:
            conn: Open SQLAlchemy Core connection.
            data: Column → value mapping.

        Returns:
            The auto-generated primary key.
        """
        # TODO(phase-1): implement
        raise NotImplementedError

    def list_history(
        self,
        conn: Connection,
        connection_id: int,
        *,
        limit: int = 50,
    ) -> list[dict]:
        """Return the most recent query history for a connection.

        Args:
            conn: Open SQLAlchemy Core connection.
            connection_id: Filter by this connection profile ID.
            limit: Maximum rows to return, newest first.

        Returns:
            List of row dicts.
        """
        # TODO(phase-1): implement
        raise NotImplementedError

    # ── SavedQuery ───────────────────────────────────────────────────────

    def list_saved_queries(self, conn: Connection, connection_id: int | None = None) -> list[dict]:
        """Return saved queries, optionally filtered by connection.

        Args:
            conn: Open SQLAlchemy Core connection.
            connection_id: If given, also include global queries (connection_id IS NULL).

        Returns:
            List of row dicts.
        """
        # TODO(phase-1): implement
        raise NotImplementedError

    def save_query(self, conn: Connection, data: dict) -> int:
        """Insert a saved query and return the new ID.

        Args:
            conn: Open SQLAlchemy Core connection.
            data: Column → value mapping.

        Returns:
            The auto-generated primary key.
        """
        # TODO(phase-1): implement
        raise NotImplementedError


def make_engine(db_path: Path) -> sa.Engine:
    """Create a synchronous SQLAlchemy engine for the app SQLite database.

    Args:
        db_path: Absolute path to the SQLite file.

    Returns:
        Configured Engine instance.
    """
    url = f"sqlite:///{db_path}"
    return create_engine(url, connect_args={"check_same_thread": False})
