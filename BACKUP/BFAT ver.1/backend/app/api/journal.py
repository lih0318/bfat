"""
Trading journal API: list entries written by engine (and legacy Rich Man).
Reads from both old journal_service and new engine accounting ledger.
"""
from fastapi import APIRouter, Query

from app.services.journal_service import clear_entries, get_entries
from app.engine.accounting import ledger

router = APIRouter()


@router.get("")
def list_journal(
    limit: int = Query(200, ge=1, le=500),
    mode: str = Query("all", description="Filter: all | live"),
    type: str | None = Query(None, description="Filter by type: entry | exit | paper_entry | paper_exit"),
):
    """Return recent journal entries (newest first). Merges old journal + new engine ledger."""
    if mode not in ("all", "live"):
        mode = "all"

    # Get entries from both sources
    old_entries = get_entries(limit=limit, mode=mode, type_filter=type)
    new_entries = ledger.get_journal_entries(limit=limit, mode=mode)

    # Merge and sort by timestamp (newest first)
    all_entries = old_entries + new_entries
    all_entries.sort(key=lambda e: e.get("ts", ""), reverse=True)

    return all_entries[:limit]


@router.delete("")
def clear_journal():
    """Clear all journal data. Irreversible."""
    clear_entries()
    ledger.clear()
    return {"ok": True}
