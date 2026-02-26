"""Log routes."""

from typing import Optional

from fastapi import APIRouter, Depends, Request

from api.deps import get_current_user

router = APIRouter(prefix="/api", tags=["logs"])


@router.get("/logs")
async def get_logs(
    request: Request,
    _: str = Depends(get_current_user),
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
