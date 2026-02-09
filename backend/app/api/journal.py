"""
Trading journal API: list entries written by Rich Man (and optional manual).
"""
from fastapi import APIRouter, Query

from app.services.journal_service import clear_entries, get_entries

router = APIRouter()


@router.get("")
def list_journal(
    limit: int = Query(200, ge=1, le=500),
    mode: str = Query("all", description="Filter: all | live"),
    type: str | None = Query(None, description="Filter by type: entry | exit | paper_entry | paper_exit"),
):
    """Return recent journal entries (newest first). Live = real trades only. type = filter by entry type."""
    if mode not in ("all", "live"):
        mode = "all"
    return get_entries(limit=limit, mode=mode, type_filter=type)


@router.delete("")
def clear_journal():
    """Clear all journal data. Irreversible."""
    clear_entries()
    return {"ok": True}
