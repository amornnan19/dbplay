"""API routes for query execution and history."""

# TODO(phase-1): Implement query execution endpoints.
# Endpoints per SPEC §6:
#   POST /api/c/{conn_id}/query               → QueryResult (HTML partial)
#   POST /api/c/{conn_id}/query/validate      → {ok, errors, dialect}
#   GET  /api/c/{conn_id}/query/history       → list[QueryHistory]
#   GET  /api/c/{conn_id}/query/autocomplete  → list[Suggestion]

from fastapi import APIRouter

router = APIRouter(prefix="/api/c", tags=["query"])
