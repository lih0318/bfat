"""
Trading journal API: list entries written by Rich Man (and optional manual).
"""
from fastapi import APIRouter, Query

from app.services.journal_service import get_entries

router = APIRouter()


@router.get("")
def list_journal(
    limit: int = Query(200, ge=1, le=500),
    mode: str = Query("all", description="Filter: all | live"),
):
    """Return recent journal entries (newest first). Live = real trades only."""
    if mode not in ("all", "live"):
        mode = "all"
    return get_entries(limit=limit, mode=mode)
