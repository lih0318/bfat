"""system_log table access."""

import json
from datetime import datetime
from typing import Any, Optional

from app.core.logging import get_logger

logger = get_logger(__name__)


class SystemLogRepository:
    """Insert and query system_log. All writes are transactional."""

    def __init__(self, db_factory):
        self._db = db_factory

    def insert(
        self,
        level: str,
        event: str,
        message: str = "",
        payload: Optional[dict] = None,
        correlation_id: Optional[str] = None,
        ts: Optional[str] = None,
    ) -> int:
        """Insert system event. Returns inserted row id."""
        conn = self._db.get_connection()
        cursor = conn.cursor()
        ts_val = ts or datetime.utcnow().isoformat() + "Z"
        try:
            payload_str = json.dumps(payload, default=str) if payload is not None else None
            cursor.execute(
                """
                INSERT INTO system_log (ts, level, event, message, payload, correlation_id)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (ts_val, level, event, message, payload_str, correlation_id),
            )
            conn.commit()
            row_id = cursor.lastrowid
            logger.info("system_log_inserted", extra={"log_id": row_id, "event": event, "level": level, "correlation_id": correlation_id})
            return row_id
        except Exception as e:
            conn.rollback()
            logger.error("system_log_insert_failed", extra={"error": str(e), "event": event})
            raise

    def query(
        self,
        level: Optional[str] = None,
        event: Optional[str] = None,
        correlation_id: Optional[str] = None,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
    ) -> list[dict[str, Any]]:
        """Query system logs. Returns list of row dicts."""
        conn = self._db.get_connection()
        conditions = []
        params = []
        if level is not None:
            conditions.append("level = ?")
            params.append(level)
        if event is not None:
            conditions.append("event = ?")
            params.append(event)
        if correlation_id is not None:
            conditions.append("correlation_id = ?")
            params.append(correlation_id)
        where = (" WHERE " + " AND ".join(conditions)) if conditions else ""
        sql = f"SELECT * FROM system_log{where} ORDER BY id DESC"
        if limit is not None:
            sql += f" LIMIT {int(limit)}"
        if offset is not None:
            sql += f" OFFSET {int(offset)}"
        cursor = conn.execute(sql, params)
        return [dict(row) for row in cursor.fetchall()]
