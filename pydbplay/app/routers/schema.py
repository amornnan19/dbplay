"""API routes for schema inspection (tables, columns, indexes)."""

# TODO(phase-1): Implement schema endpoints.
# Endpoints per SPEC §6:
#   GET /api/c/{conn_id}/schemas              → list[str]
#   GET /api/c/{conn_id}/tables?schema=X      → list[TableInfo] (HTML partial)
#   GET /api/c/{conn_id}/tables/{table}       → TableSchema

from fastapi import APIRouter

router = APIRouter(prefix="/api/c", tags=["schema"])
