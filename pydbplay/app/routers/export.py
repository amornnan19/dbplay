"""API routes for data export (Phase 3)."""

# TODO(phase-3): Implement export endpoints.
# Endpoints per SPEC §6:
#   POST /api/c/{conn_id}/export?format=csv|json|sql  → file stream

from fastapi import APIRouter

router = APIRouter(prefix="/api/c", tags=["export"])
