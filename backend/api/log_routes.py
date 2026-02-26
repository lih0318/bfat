"""Log routes."""

from typing import Optional

from fastapi import APIRouter, Request

router = APIRouter(prefix="/api", tags=["logs"])


@router.get("/logs")
async def get_logs(
    request: Request,
    level: Optional[str] = None,
    limit: Optional[int] = 100,
    offset: Optional[int] = 0,
):
    """Return system logs from database."""
    db = getattr(request.app.state, "db", None)
    if db is None:
        return []
    from app.persistence.system_log import SystemLogRepository

    repo = SystemLogRepository(db)
    rows = repo.query(level=level, limit=limit, offset=offset)
    return [
        {
            "id": row["id"],
            "ts": row["ts"],
            "level": row["level"],
            "event": row["event"],
            "message": row["message"] or "",
            "payload": row["payload"],
            "correlation_id": row["correlation_id"],
        }
        for row in rows
    ]
