"""
Accounting: unified event log + Journal-compatible persistence.

Event types:
  - signal_snapshot: TrendScore for all symbols at signal_tick
  - order: order placed / filled / cancelled
  - fill: trade fill event
  - bracket: SL/TP set/triggered
  - funding: funding fee event
  - equity_snapshot: periodic equity record
  - exit: position closed
"""
from __future__ import annotations

import json
import logging
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.core.config import settings

logger = logging.getLogger(__name__)


class AccountingLedger:
    """In-memory event log with JSON file persistence."""

    def __init__(self) -> None:
        self._events: list[dict[str, Any]] = []
        self._max_events = 2000
        self._loaded = False

    def _path(self) -> Path:
        p = settings.config_dir / "engine_ledger.json"
        settings.config_dir.mkdir(parents=True, exist_ok=True)
        return p

    def _load(self) -> None:
        if self._loaded:
            return
        path = self._path()
        if path.exists():
            try:
                with open(path, encoding="utf-8") as f:
                    data = json.load(f)
                self._events = data if isinstance(data, list) else []
            except Exception as exc:
                logger.warning("accounting: load failed: %s", exc)
                self._events = []
        self._loaded = True

    def _save(self) -> None:
        path = self._path()
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(self._events[-self._max_events:], f, indent=2, ensure_ascii=False)
        except Exception as exc:
            logger.warning("accounting: save failed: %s", exc)

    def _now_iso(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def record(self, event_type: str, data: dict[str, Any]) -> None:
        """Record a generic event."""
        self._load()
        event = {
            "ts": self._now_iso(),
            "event": event_type,
            **data,
        }
        self._events.append(event)
        if len(self._events) > self._max_events:
            self._events = self._events[-self._max_events:]
        self._save()

    def record_signal_snapshot(self, snapshots: dict[str, Any]) -> None:
        """Record all TrendScores at signal_tick."""
        self.record("signal_snapshot", {"signals": snapshots})

    def record_order(self, symbol: str, side: str, qty: float, order_type: str, **extra: Any) -> None:
        self.record("order", {"symbol": symbol, "side": side, "qty": qty, "order_type": order_type, **extra})

    def record_fill(
        self,
        symbol: str,
        side: str,
        qty: float,
        price: float,
        realized_pnl: float = 0.0,
        fee: float = 0.0,
        **extra: Any,
    ) -> None:
        self.record("fill", {
            "symbol": symbol, "side": side, "qty": qty, "price": price,
            "realized_pnl": realized_pnl, "fee": fee, **extra,
        })

    def record_funding(self, symbol: str, amount: float, rate: float) -> None:
        self.record("funding", {"symbol": symbol, "amount": amount, "rate": rate})

    def record_equity_snapshot(self, equity: float, positions: list[dict[str, Any]] | None = None) -> None:
        self.record("equity_snapshot", {
            "equity": equity,
            "positions": positions or [],
        })

    def record_exit(
        self,
        symbol: str,
        side: str,
        entry_price: float,
        exit_price: float,
        qty: float,
        realized_pnl: float,
        **extra: Any,
    ) -> None:
        self.record("exit", {
            "symbol": symbol, "side": side,
            "entry_price": entry_price, "exit_price": exit_price,
            "qty": qty, "realized_pnl": realized_pnl,
            **extra,
        })

    # ── Journal compatibility ────────────────────────────────────

    def get_journal_entries(self, limit: int = 200, mode: str = "all") -> list[dict[str, Any]]:
        """
        Convert ledger events to Journal-compatible format.
        Returns newest first.
        """
        self._load()
        journal_types = {"fill", "exit", "order"}
        entries: list[dict[str, Any]] = []

        for ev in reversed(self._events):
            if len(entries) >= limit:
                break
            et = ev.get("event", "")

            if et == "fill":
                pnl = ev.get("realized_pnl", 0)
                entry = {
                    "id": f"eng_{ev.get('ts', '')}",
                    "ts": ev.get("ts", ""),
                    "type": "exit" if pnl != 0 else "entry",
                    "symbol": ev.get("symbol", ""),
                    "side": ev.get("side", ""),
                    "entry_price": ev.get("price"),
                    "exit_price": ev.get("price") if pnl != 0 else None,
                    "qty": ev.get("qty"),
                    "realized_pnl": round(pnl, 4) if pnl else None,
                }
                if mode == "live" and entry["type"] not in ("entry", "exit"):
                    continue
                entries.append(entry)

            elif et == "exit":
                entry = {
                    "id": f"eng_{ev.get('ts', '')}",
                    "ts": ev.get("ts", ""),
                    "type": "exit",
                    "symbol": ev.get("symbol", ""),
                    "side": ev.get("side", ""),
                    "entry_price": ev.get("entry_price"),
                    "exit_price": ev.get("exit_price"),
                    "qty": ev.get("qty"),
                    "realized_pnl": round(ev.get("realized_pnl", 0), 4),
                }
                entries.append(entry)

            elif et == "order" and mode == "all":
                entry = {
                    "id": f"eng_{ev.get('ts', '')}",
                    "ts": ev.get("ts", ""),
                    "type": "entry",
                    "symbol": ev.get("symbol", ""),
                    "side": ev.get("side", ""),
                    "qty": ev.get("qty"),
                }
                entries.append(entry)

        return entries

    def get_activity(self, limit: int = 100, mode: str = "all") -> list[dict[str, Any]]:
        """Get recent events as activity log entries (for UI)."""
        self._load()
        result: list[dict[str, Any]] = []
        live_types = ("fill", "exit", "order", "exec_tick_skip", "exec_tick_summary",
                      "tp1_filled", "tp2_filled", "sl_moved_to_breakeven", "sl_triggered",
                      "chandelier_sl_updated")
        for ev in reversed(self._events):
            if len(result) >= limit:
                break
            et = ev.get("event", "")
            if mode == "live" and et not in live_types:
                continue
            result.append({
                "ts": ev.get("ts", ""),
                "type": et,
                "symbol": ev.get("symbol", ""),
                "message": self._event_to_message(ev),
            })
        return result

    def _event_to_message(self, ev: dict[str, Any]) -> str:
        et = ev.get("event", "")
        sym = ev.get("symbol", "")
        if et == "fill":
            return (f"Fill {ev.get('side', '')} {sym} "
                    f"qty={ev.get('qty', 0):.6f} @ {ev.get('price', 0):.2f} "
                    f"pnl={ev.get('realized_pnl', 0):.4f}")
        if et == "exit":
            return (f"Exit {sym} pnl={ev.get('realized_pnl', 0):.4f}")
        if et == "order":
            return (f"Order {ev.get('order_type', '')} {ev.get('side', '')} {sym} "
                    f"qty={ev.get('qty', 0):.6f}")
        if et == "exec_tick_skip":
            return ev.get("message", "Exec tick skipped (no targets).")
        if et == "exec_tick_summary":
            orders = ev.get("orders_placed", 0)
            reasons = ev.get("skip_reasons", [])
            targets = ev.get("targets_count", 0)
            if orders > 0:
                return f"Exec tick: {orders} order(s) placed (targets={targets})."
            # No orders: explain why
            reason_counts = Counter(r.get("reason", "") for r in reasons)
            parts = [f"{cnt}× {reason}" for reason, cnt in sorted(reason_counts.items())]
            reason_str = ", ".join(parts) if parts else "no deltas"
            return f"Exec tick: no orders (targets={targets}). Reasons: {reason_str}."
        if et == "signal_snapshot":
            n = len(ev.get("signals", {}))
            return f"Signal snapshot: {n} symbols"
        if et == "equity_snapshot":
            return f"Equity: {ev.get('equity', 0):.2f} USDT"
        if et == "funding":
            return f"Funding {sym}: {ev.get('amount', 0):.4f} USDT"
        if et == "tp1_filled":
            return f"TP1 체결 {sym} @ {ev.get('message', '')}"
        if et == "tp2_filled":
            return f"TP2 체결 {sym} @ {ev.get('message', '')}"
        if et == "sl_moved_to_breakeven":
            return f"SL → BE 전환 {sym}: {ev.get('message', '')}"
        if et == "sl_triggered":
            return f"SL 트리거 {sym} @ {ev.get('message', '')}"
        if et == "chandelier_sl_updated":
            return f"Chandelier SL 갱신 {sym}: {ev.get('message', '')}"
        return f"{et}: {sym}"

    def clear(self) -> None:
        self._events.clear()
        self._save()


# Module-level singleton
ledger = AccountingLedger()
