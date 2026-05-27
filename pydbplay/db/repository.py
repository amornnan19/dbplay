"""SQLAlchemy Core Table definitions and Repository for app-internal SQLite.

Uses SQLAlchemy Core (Table + insert()/select()) — NOT the ORM.
Migration version tracked via SQLite PRAGMA user_version (no Alembic).
"""

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import sqlalchemy as sa
from sqlalchemy import Connection, MetaData, Table, create_engine, event, text

from pydbplay.db.models import ConnectionProfile, QueryHistory, SavedQuery
from pydbplay.schemas.connection import ConnectionCreate, ConnectionUpdate, SavedQueryUpdate

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


def _row_to_connection(row: sa.engine.Row[tuple[object, ...]]) -> ConnectionProfile:
    """Map a SQLAlchemy Core Row to a ConnectionProfile model."""
    return ConnectionProfile.model_validate(dict(row._mapping))


def _row_to_history(row: sa.engine.Row[tuple[object, ...]]) -> QueryHistory:
    """Map a SQLAlchemy Core Row to a QueryHistory model."""
    return QueryHistory.model_validate(dict(row._mapping))


def _row_to_saved_query(row: sa.engine.Row[tuple[object, ...]]) -> SavedQuery:
    """Map a SQLAlchemy Core Row to a SavedQuery model."""
    return SavedQuery.model_validate(dict(row._mapping))


def _now() -> datetime:
    """Return the current UTC datetime (timezone-naive, stored as ISO-8601 TEXT)."""
    return datetime.now(UTC).replace(tzinfo=None)


class Repository:
    """CRUD operations for app-internal SQLite via SQLAlchemy Core.

    Holds a SQLAlchemy Engine; opens a connection per operation.
    Write operations use ``engine.begin()`` (auto commit/rollback).
    Read operations use ``engine.connect()``.

    This class must NOT import FastAPI.
    """

    def __init__(self, engine: sa.Engine) -> None:
        self._engine = engine

    # ── ConnectionProfile ────────────────────────────────────────────────

    def create_connection(self, data: ConnectionCreate) -> ConnectionProfile:
        """Insert a new connection profile and return the full persisted model.

        Args:
            data: Validated ConnectionCreate input.

        Returns:
            The newly created ConnectionProfile with assigned id and timestamps.
        """
        now = _now()
        row_data = {
            "name": data.name,
            "engine": data.engine,
            "host": data.host,
            "port": data.port,
            "database": data.database,
            "username": data.username,
            # Repository stores password_encrypted as an opaque string.
            # Encryption/decryption is handled outside this layer.
            "password_encrypted": data.password,
            "ssl_mode": data.ssl_mode,
            "read_only": data.read_only,
            "color": data.color,
            "created_at": now,
            "updated_at": now,
            "last_used_at": None,
        }
        with self._engine.begin() as conn:
            result = conn.execute(
                sa.insert(connection_profile_table).values(**row_data)
            )
            pk = result.inserted_primary_key
            assert pk is not None
            new_id: int = pk[0]
            row = conn.execute(
                sa.select(connection_profile_table).where(
                    connection_profile_table.c.id == new_id
                )
            ).one()
        return _row_to_connection(row)

    def get_connection(self, conn_id: int) -> ConnectionProfile | None:
        """Fetch a single connection profile by primary key.

        Args:
            conn_id: Primary key of the profile.

        Returns:
            ConnectionProfile or None if not found.
        """
        with self._engine.connect() as conn:
            row = conn.execute(
                sa.select(connection_profile_table).where(
                    connection_profile_table.c.id == conn_id
                )
            ).one_or_none()
        if row is None:
            return None
        return _row_to_connection(row)

    def list_connections(self) -> list[ConnectionProfile]:
        """Return all connection profiles ordered by last_used_at DESC (NULLs last), then created_at DESC.

        Returns:
            List of ConnectionProfile models.
        """
        with self._engine.connect() as conn:
            rows = conn.execute(
                sa.select(connection_profile_table).order_by(
                    connection_profile_table.c.last_used_at.desc().nulls_last(),
                    connection_profile_table.c.created_at.desc(),
                )
            ).fetchall()
        return [_row_to_connection(r) for r in rows]

    def update_connection(
        self, conn_id: int, data: ConnectionUpdate
    ) -> ConnectionProfile | None:
        """Partially update a connection profile (only fields set in data).

        Args:
            conn_id: Profile to update.
            data: ConnectionUpdate with only the fields that should change.

        Returns:
            Updated ConnectionProfile or None if the profile was not found.
        """
        updates = data.model_dump(exclude_unset=True)

        # Remap plaintext "password" to "password_encrypted"
        if "password" in updates:
            updates["password_encrypted"] = updates.pop("password")

        updates["updated_at"] = _now()

        with self._engine.begin() as conn:
            result = conn.execute(
                sa.update(connection_profile_table)
                .where(connection_profile_table.c.id == conn_id)
                .values(**updates)
            )
            if result.rowcount == 0:
                return None
            row = conn.execute(
                sa.select(connection_profile_table).where(
                    connection_profile_table.c.id == conn_id
                )
            ).one()
        return _row_to_connection(row)

    def delete_connection(self, conn_id: int) -> bool:
        """Delete a connection profile by primary key.

        Args:
            conn_id: Profile to delete.

        Returns:
            True if a row was deleted, False if the id did not exist.
        """
        with self._engine.begin() as conn:
            result = conn.execute(
                sa.delete(connection_profile_table).where(
                    connection_profile_table.c.id == conn_id
                )
            )
        return result.rowcount > 0

    def touch_last_used(self, conn_id: int) -> None:
        """Set last_used_at to the current UTC time for a connection profile.

        Args:
            conn_id: Profile to touch.
        """
        with self._engine.begin() as conn:
            conn.execute(
                sa.update(connection_profile_table)
                .where(connection_profile_table.c.id == conn_id)
                .values(last_used_at=_now())
            )

    # ── QueryHistory ─────────────────────────────────────────────────────

    def add_history(self, entry: QueryHistory) -> QueryHistory:
        """Insert a query history record and return it with the assigned id.

        Args:
            entry: QueryHistory model (id field is ignored; a new one is assigned).

        Returns:
            QueryHistory with the auto-assigned primary key.
        """
        row_data = {
            "connection_id": entry.connection_id,
            "sql": entry.sql,
            "executed_at": entry.executed_at,
            "duration_ms": entry.duration_ms,
            "row_count": entry.row_count,
            "success": entry.success,
            "error_message": entry.error_message,
        }
        with self._engine.begin() as conn:
            result = conn.execute(
                sa.insert(query_history_table).values(**row_data)
            )
            pk = result.inserted_primary_key
            assert pk is not None
            new_id: int = pk[0]
            row = conn.execute(
                sa.select(query_history_table).where(
                    query_history_table.c.id == new_id
                )
            ).one()
        return _row_to_history(row)

    def list_history(
        self, connection_id: int, limit: int = 50
    ) -> list[QueryHistory]:
        """Return the most recent query history for a connection.

        Args:
            connection_id: Filter by this connection profile id.
            limit: Maximum rows to return (newest first).

        Returns:
            List of QueryHistory models ordered by executed_at DESC.
        """
        with self._engine.connect() as conn:
            rows = conn.execute(
                sa.select(query_history_table)
                .where(query_history_table.c.connection_id == connection_id)
                .order_by(query_history_table.c.executed_at.desc())
                .limit(limit)
            ).fetchall()
        return [_row_to_history(r) for r in rows]

    # ── SavedQuery ───────────────────────────────────────────────────────

    def create_saved_query(
        self,
        name: str,
        sql: str,
        connection_id: int | None = None,
        description: str | None = None,
    ) -> SavedQuery:
        """Insert a new saved query and return the persisted model.

        Args:
            name: Human-friendly label for the query.
            sql: The SQL text to save.
            connection_id: Optional connection to associate with (None = global).
            description: Optional longer description.

        Returns:
            SavedQuery with the assigned id and timestamps.
        """
        now = _now()
        row_data = {
            "connection_id": connection_id,
            "name": name,
            "sql": sql,
            "description": description,
            "created_at": now,
            "updated_at": now,
        }
        with self._engine.begin() as conn:
            result = conn.execute(
                sa.insert(saved_query_table).values(**row_data)
            )
            pk = result.inserted_primary_key
            assert pk is not None
            new_id: int = pk[0]
            row = conn.execute(
                sa.select(saved_query_table).where(
                    saved_query_table.c.id == new_id
                )
            ).one()
        return _row_to_saved_query(row)

    def get_saved_query(self, query_id: int) -> SavedQuery | None:
        """Fetch a single saved query by primary key.

        Args:
            query_id: Primary key of the saved query.

        Returns:
            SavedQuery or None if not found.
        """
        with self._engine.connect() as conn:
            row = conn.execute(
                sa.select(saved_query_table).where(
                    saved_query_table.c.id == query_id
                )
            ).one_or_none()
        if row is None:
            return None
        return _row_to_saved_query(row)

    def list_saved_queries(
        self, connection_id: int | None = None
    ) -> list[SavedQuery]:
        """Return saved queries filtered by connection visibility.

        When connection_id is None, return only global queries (connection_id IS NULL).
        When connection_id is given, return that connection's queries AND global queries.

        Args:
            connection_id: Connection filter; None returns only globals.

        Returns:
            List of SavedQuery models.
        """
        with self._engine.connect() as conn:
            if connection_id is None:
                stmt = sa.select(saved_query_table).where(
                    saved_query_table.c.connection_id.is_(None)
                )
            else:
                stmt = sa.select(saved_query_table).where(
                    sa.or_(
                        saved_query_table.c.connection_id == connection_id,
                        saved_query_table.c.connection_id.is_(None),
                    )
                )
            rows = conn.execute(stmt).fetchall()
        return [_row_to_saved_query(r) for r in rows]

    def update_saved_query(
        self,
        query_id: int,
        data: SavedQueryUpdate,
    ) -> SavedQuery | None:
        """Partially update a saved query (only fields set in data).

        Args:
            query_id: Primary key of the saved query.
            data: SavedQueryUpdate with only the fields that should change.
                  Pass ``description=None`` explicitly to clear the column.

        Returns:
            Updated SavedQuery or None if not found.
        """
        updates: dict[str, object] = data.model_dump(exclude_unset=True)
        updates["updated_at"] = _now()

        with self._engine.begin() as conn:
            result = conn.execute(
                sa.update(saved_query_table)
                .where(saved_query_table.c.id == query_id)
                .values(**updates)
            )
            if result.rowcount == 0:
                return None
            row = conn.execute(
                sa.select(saved_query_table).where(
                    saved_query_table.c.id == query_id
                )
            ).one()
        return _row_to_saved_query(row)

    def delete_saved_query(self, query_id: int) -> bool:
        """Delete a saved query by primary key.

        Args:
            query_id: Primary key of the saved query.

        Returns:
            True if a row was deleted, False if the id did not exist.
        """
        with self._engine.begin() as conn:
            result = conn.execute(
                sa.delete(saved_query_table).where(
                    saved_query_table.c.id == query_id
                )
            )
        return result.rowcount > 0


def make_engine(db_path: Path) -> sa.Engine:
    """Create a synchronous SQLAlchemy engine for the app SQLite database.

    SQLite does not enforce foreign keys by default; we register a ``connect``
    event listener that issues ``PRAGMA foreign_keys=ON`` on every new
    connection so that ON DELETE CASCADE / ON DELETE SET NULL fire correctly.

    Args:
        db_path: Absolute path to the SQLite file.

    Returns:
        Configured Engine instance.
    """
    url = f"sqlite:///{db_path}"
    engine = create_engine(url, connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
    def _enable_sqlite_fk(dbapi_conn: object, _record: object) -> None:
        assert isinstance(dbapi_conn, sqlite3.Connection)
        dbapi_conn.execute("PRAGMA foreign_keys=ON")

    return engine
