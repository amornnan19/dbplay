"""Pydantic schemas for schema inspection API endpoints.

These are also used by adapters/base.py as the return types for
list_tables() and describe_table().
"""

from pydantic import BaseModel, Field


class ColumnInfo(BaseModel):
    """Metadata for a single table column."""

    name: str
    data_type: str
    """Engine-native type string, e.g. "varchar(255)", "integer", "jsonb"."""

    is_nullable: bool = True
    is_primary_key: bool = False
    default_value: str | None = None
    """SQL default expression as a string, or None if no default."""

    comment: str | None = None


class IndexInfo(BaseModel):
    """Metadata for a table index."""

    name: str
    columns: list[str]
    is_unique: bool = False
    is_primary: bool = False


class ForeignKeyInfo(BaseModel):
    """Metadata for a foreign key constraint."""

    name: str | None = None
    columns: list[str]
    ref_table: str
    ref_columns: list[str]
    on_delete: str | None = None
    on_update: str | None = None


class TableInfo(BaseModel):
    """Summary of a single table (used in list_tables() results)."""

    name: str
    schema_name: str | None = Field(None, alias="schema")
    """DB schema that owns this table (alias "schema" for JSON serialization)."""
    table_type: str = "BASE TABLE"
    """One of: "BASE TABLE", "VIEW", "FOREIGN"."""

    row_count_estimate: int | None = None
    """Approximate row count from DB statistics; may be None."""

    model_config = {"populate_by_name": True}


class TableSchema(BaseModel):
    """Full schema metadata for a single table."""

    name: str
    schema_name: str | None = Field(None, alias="schema")
    """DB schema that owns this table (alias "schema" for JSON serialization)."""
    columns: list[ColumnInfo]
    indexes: list[IndexInfo] = []
    foreign_keys: list[ForeignKeyInfo] = []
    ddl: str | None = None
    """CREATE TABLE DDL statement if available."""

    model_config = {"populate_by_name": True}
