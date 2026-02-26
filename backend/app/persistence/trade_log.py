"""trade_log table access."""

import json
from typing import Any, Optional

from app.core.logging import get_logger

logger = get_logger(__name__)


class TradeRepository:
    """Insert and query trade_log. All writes are transactional."""

    def __init__(self, db_factory):
        self._db = db_factory

    def insert(
        self,
        symbol: str,
        side: str,
        entry_time: str,
        entry_price: float,
        size: float,
        exit_time: str,
        exit_price: float,
        pnl: float,
        pnl_r: Optional[float] = None,
        stop_phase: Optional[str] = None,
        signal_candle_ts: Optional[str] = None,
        correlation_id: Optional[str] = None,
        metadata: Optional[dict] = None,
    ) -> int:
        """Insert trade record. Returns inserted row id."""
        conn = self._db.get_connection()
        cursor = conn.cursor()
        try:
            metadata_str = json.dumps(metadata) if metadata is not None else None
            cursor.execute(
                """
                INSERT INTO trade_log (
                    symbol, side, entry_time, entry_price, size,
                    exit_time, exit_price, pnl, pnl_r, stop_phase,
                    signal_candle_ts, correlation_id, metadata
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    symbol, side, entry_time, entry_price, size,
                    exit_time, exit_price, pnl, pnl_r, stop_phase,
                    signal_candle_ts, correlation_id, metadata_str,
                ),
            )
            conn.commit()
            row_id = cursor.lastrowid
            logger.info("trade_inserted", extra={"trade_id": row_id, "symbol": symbol, "correlation_id": correlation_id})
            return row_id
        except Exception as e:
            conn.rollback()
            logger.error("trade_insert_failed", extra={"error": str(e), "symbol": symbol})
            raise

    def query(
        self,
        symbol: Optional[str] = None,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
        correlation_id: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        """Query trades. Returns list of row dicts."""
        conn = self._db.get_connection()
        conditions = []
        params = []
        if symbol is not None:
            conditions.append("symbol = ?")
            params.append(symbol)
        if correlation_id is not None:
            conditions.append("correlation_id = ?")
            params.append(correlation_id)
        where = (" WHERE " + " AND ".join(conditions)) if conditions else ""
        sql = f"SELECT * FROM trade_log{where} ORDER BY id DESC"
        if limit is not None:
            sql += f" LIMIT {int(limit)}"
        if offset is not None:
            sql += f" OFFSET {int(offset)}"
        cursor = conn.execute(sql, params)
        return [dict(row) for row in cursor.fetchall()]
