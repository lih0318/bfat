"""
Balance history: store periodic snapshots of total margin balance for Wallet chart.
Max 30 days of data; snapshot throttled to once per hour to limit file size.
"""
import json
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

from app.core.config import settings

logger = logging.getLogger(__name__)

_ENTRIES: list[dict[str, Any]] = []
_LOADED = False
_SNAPSHOT_INTERVAL_SEC = 3600  # 1 hour
_LAST_TS: float = 0
_MAX_DAYS = 30


def _path() -> Path:
    return settings.balance_history_path


def _load() -> list[dict[str, Any]]:
    global _ENTRIES, _LOADED
    if _LOADED:
        return _ENTRIES
    path = _path()
    if path.exists():
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
                _ENTRIES = data if isinstance(data, list) else []
        except Exception as e:
            logger.warning("Failed to load balance history: %s", e)
            _ENTRIES = []
    else:
        _ENTRIES = []
    _LOADED = True
    return _ENTRIES


def _save(entries: list[dict[str, Any]]) -> None:
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(entries, f, indent=2)


def _prune(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep only last MAX_DAYS days."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=_MAX_DAYS)).timestamp()
    return [e for e in entries if (e.get("ts_epoch") or 0) >= cutoff]


def record_snapshot(total_margin_balance: float) -> None:
    """Append a balance snapshot if at least SNAPSHOT_INTERVAL_SEC has passed. Prune to 30 days."""
    global _ENTRIES, _LAST_TS
    now = datetime.now(timezone.utc)
    now_ts = now.timestamp()
    if now_ts - _LAST_TS < _SNAPSHOT_INTERVAL_SEC:
        return
    _LAST_TS = now_ts
    entries = _load()
    entries.append({
        "ts": now.isoformat(),
        "ts_epoch": int(now_ts),
        "balance": round(total_margin_balance, 2),
    })
    entries = _prune(entries)
    _ENTRIES = entries
    _save(entries)
    logger.debug("Balance history snapshot: balance=%.2f", total_margin_balance)


def get_history(range_hours: int) -> list[dict[str, Any]]:
    """Return points for the last range_hours. range_hours: 24 for 1d, 168 for 1w."""
    entries = _load()
    cutoff_ts = (datetime.now(timezone.utc).timestamp() - range_hours * 3600)
    out = [e for e in entries if (e.get("ts_epoch") or 0) >= cutoff_ts]
    return sorted(out, key=lambda x: x.get("ts_epoch", 0))
