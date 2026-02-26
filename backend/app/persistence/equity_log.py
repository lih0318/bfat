"""equity_log table access."""

from typing import Any, Optional

from app.core.logging import get_logger

logger = get_logger(__name__)


class EquityRepository:
    """Insert and query equity_log. All writes are transactional."""

    def __init__(self, db_factory):
        self._db = db_factory

    def insert(
        self,
        ts: str,
        equity: float,
        available_balance: Optional[float] = None,
        unrealized_pnl: Optional[float] = None,
        daily_start_equity: Optional[float] = None,
    ) -> int:
        """Insert equity snapshot. Returns inserted row id."""
        conn = self._db.get_connection()
        cursor = conn.cursor()
        try:
            cursor.execute(
                """
                INSERT INTO equity_log (ts, equity, available_balance, unrealized_pnl, daily_start_equity)
                VALUES (?, ?, ?, ?, ?)
                """,
                (ts, equity, available_balance, unrealized_pnl, daily_start_equity),
            )
            conn.commit()
            row_id = cursor.lastrowid
            logger.info("equity_inserted", extra={"equity_id": row_id, "equity": equity})
            return row_id
        except Exception as e:
            conn.rollback()
            logger.error("equity_insert_failed", extra={"error": str(e)})
            raise

    def query(
        self,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
    ) -> list[dict[str, Any]]:
        """Query equity history. Returns list of row dicts."""
        conn = self._db.get_connection()
        sql = "SELECT * FROM equity_log ORDER BY id DESC"
        if limit is not None:
            sql += f" LIMIT {int(limit)}"
        if offset is not None:
            sql += f" OFFSET {int(offset)}"
        cursor = conn.execute(sql)
        return [dict(row) for row in cursor.fetchall()]
