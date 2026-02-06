"""
Trading journal: persist entries to JSON file. Rich Man appends on entry/exit.
"""
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.core.config import settings

logger = logging.getLogger(__name__)

_JOURNAL: list[dict[str, Any]] = []
_LOADED = False


def _path() -> Path:
    return settings.journal_path


def _load() -> list[dict[str, Any]]:
    global _JOURNAL, _LOADED
    if _LOADED:
        return _JOURNAL
    path = _path()
    if path.exists():
        try:
            with open(path, encoding="utf-8") as f:
                _JOURNAL = json.load(f)
                if not isinstance(_JOURNAL, list):
                    _JOURNAL = []
        except Exception as e:
            logger.warning("Failed to load journal: %s", e)
            _JOURNAL = []
    else:
        _JOURNAL = []
    _LOADED = True
    return _JOURNAL


def _save(entries: list[dict[str, Any]]) -> None:
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(entries, f, indent=2, ensure_ascii=False)


def append_entry(entry: dict[str, Any]) -> None:
    """Append a journal entry (entry or exit)."""
    global _JOURNAL
    entries = _load()
    entry["ts"] = datetime.now(timezone.utc).isoformat()
    if "id" not in entry:
        entry["id"] = entry.get("client_order_id") or f"j_{len(entries)}"
    entries.append(entry)
    _JOURNAL = entries
    _save(entries)
    logger.info("Journal appended: %s %s", entry.get("type"), entry.get("symbol"))


def get_entries(limit: int = 200, mode: str = "all") -> list[dict[str, Any]]:
    """Return most recent entries (newest first). mode: all | live."""
    entries = _load()
    if mode == "live":
        entries = [e for e in entries if e.get("type") in ("entry", "exit")]
    return list(reversed(entries[-limit:]))  # newest first
