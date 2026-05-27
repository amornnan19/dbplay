"""API routes for row-level CRUD (Phase 2)."""

# TODO(phase-2): Implement row CRUD endpoints.
# Endpoints per SPEC §6:
#   GET    /api/c/{conn_id}/rows/{table}?page=1&filter=...  → paginated rows
#   PATCH  /api/c/{conn_id}/rows/{table}/{pk}               → updated row
#   POST   /api/c/{conn_id}/rows/{table}                    → inserted row
#   DELETE /api/c/{conn_id}/rows/{table}/{pk}               → 204

from fastapi import APIRouter

router = APIRouter(prefix="/api/c", tags=["rows"])
