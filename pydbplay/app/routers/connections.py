"""API routes for connection profile CRUD."""

# TODO(phase-1): Implement connection CRUD endpoints.
# Endpoints per SPEC §6:
#   GET    /api/connections           → list[ConnectionProfile]
#   POST   /api/connections           → ConnectionProfile
#   POST   /api/connections/test      → {ok, message}
#   PATCH  /api/connections/{id}      → ConnectionProfile
#   DELETE /api/connections/{id}      → 204
#   POST   /api/connections/{id}/connect → partial HTML

from fastapi import APIRouter

router = APIRouter(prefix="/api/connections", tags=["connections"])
