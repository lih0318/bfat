"""
Execution engine: delta trading, order slicing, fill-aware bracket management,
OCO emulation for SL/TP.

Responsibilities:
  - Compare target_qty vs current_qty → compute delta
  - Place orders (LIMIT/IOC/MARKET per config)
  - Manage SL/TP brackets after fills
  - Slice large orders into smaller parts
  - Cancel stale orders
"""
from __future__ import annotations

import logging
import math
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

from app.engine.config_model import EngineConfig
from app.engine.sizing import TargetPosition
from app.services.binance_client import binance_client
from app.services.exchange_info import ExchangeInfoCache

logger = logging.getLogger(__name__)

# Maximum number of slices for a single order
MAX_SLICES = 5
# Seconds before a pending limit order is considered stale
STALE_ORDER_SEC = 120


@dataclass
class OrderState:
    """Per-symbol order tracking."""
    symbol: str
    pending_order_id: Optional[str] = None
    pending_side: Optional[str] = None
    pending_qty: float = 0.0
    pending_placed_at: float = 0.0
    filled_qty: float = 0.0
    active_sl_id: Optional[str] = None
    active_tp_id: Optional[str] = None
    last_bracket_entry_price: float = 0.0


class ExecutionEngine:
    """Manages order placement and bracket (SL/TP) lifecycle."""

    def __init__(self) -> None:
        self._states: dict[str, OrderState] = {}
        self._activity: list[dict[str, Any]] = []
        self._max_activity = 200

    @property
    def activity_log(self) -> list[dict[str, Any]]:
        return list(self._activity)

    def _log(self, typ: str, symbol: str, msg: str, **extra: Any) -> None:
        from datetime import datetime, timezone
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "type": typ,
            "symbol": symbol,
            "message": msg,
            **extra,
        }
        self._activity.append(entry)
        if len(self._activity) > self._max_activity:
            self._activity = self._activity[-self._max_activity:]
        logger.info("[exec] %s %s: %s", typ, symbol, msg)

    def _get_state(self, symbol: str) -> OrderState:
        if symbol not in self._states:
            self._states[symbol] = OrderState(symbol=symbol)
        return self._states[symbol]

    # ── Main tick ────────────────────────────────────────────────

    def tick(
        self,
        targets: dict[str, TargetPosition],
        config: EngineConfig,
        equity: float,
    ) -> None:
        """
        Single execution tick:
        1. Fetch current positions
        2. Compute deltas
        3. Cancel stale orders
        4. Place new orders for significant deltas
        5. Manage brackets
        """
        # Fetch all positions
        try:
            all_positions = binance_client.position_information()
        except Exception as exc:
            logger.error("execution tick: position fetch failed: %s", exc)
            return

        current_map: dict[str, dict[str, Any]] = {}
        for p in all_positions:
            amt = float(p.get("positionAmt", 0))
            if amt == 0:
                continue
            sym = p.get("symbol", "")
            current_map[sym] = {
                "side": "LONG" if amt > 0 else "SHORT",
                "qty": abs(amt),
                "entry_price": float(p.get("entryPrice", 0)),
                "notional": abs(amt) * float(p.get("entryPrice", 0)),
            }

        # Process targets: open/adjust positions
        threshold_notional = equity * config.execution_threshold_pct

        for sym, target in targets.items():
            state = self._get_state(sym)
            current = current_map.get(sym)

            # Cancel stale pending orders
            self._cancel_stale_orders(state, config)

            if state.pending_order_id:
                # Already have a pending order — skip
                continue

            current_qty = 0.0
            current_side: Optional[str] = None
            if current:
                current_qty = current["qty"]
                current_side = current["side"]

            # Compute delta
            target_signed = target.target_qty if target.side == "LONG" else -target.target_qty
            current_signed = current_qty if current_side == "LONG" else -current_qty if current_side else 0.0
            delta = target_signed - current_signed

            if abs(delta) * self._get_price(sym) < threshold_notional:
                continue  # delta too small

            # Determine order side and qty
            if delta > 0:
                order_side = "BUY"
                order_qty = abs(delta)
            else:
                order_side = "SELL"
                order_qty = abs(delta)

            # If we need to flip (close existing + open opposite), handle close first
            if current_side and ((current_side == "LONG" and delta < -current_qty) or
                                 (current_side == "SHORT" and delta > current_qty)):
                self._close_position(sym, current, state)
                # Recalculate remaining delta
                order_qty = abs(abs(delta) - current_qty)
                if order_qty * self._get_price(sym) < threshold_notional:
                    continue

            # Round qty
            order_qty = ExchangeInfoCache.round_quantity(sym, order_qty)
            if order_qty <= 0:
                continue

            # Check reduce_only
            reduce_only = False
            if current_side:
                if (current_side == "LONG" and order_side == "SELL" and order_qty <= current_qty):
                    reduce_only = True
                elif (current_side == "SHORT" and order_side == "BUY" and order_qty <= current_qty):
                    reduce_only = True

            # Slice large orders
            slices = self._compute_slices(order_qty, sym)

            for slice_qty in slices:
                self._place_order(sym, order_side, slice_qty, config, state, reduce_only)
                if len(slices) > 1:
                    time.sleep(0.3)  # brief pause between slices

        # Close positions for symbols NOT in targets
        for sym, current in current_map.items():
            if sym not in targets:
                state = self._get_state(sym)
                self._close_position(sym, current, state)

    # ── Order placement ──────────────────────────────────────────

    def _place_order(
        self,
        symbol: str,
        side: str,
        qty: float,
        config: EngineConfig,
        state: OrderState,
        reduce_only: bool = False,
    ) -> None:
        client_id = f"eng_{uuid.uuid4().hex[:12]}"
        mode = config.entry_order_mode

        try:
            if mode == "MARKET":
                binance_client.new_order(
                    symbol=symbol,
                    side=side,
                    order_type="MARKET",
                    quantity=qty,
                    reduce_only=reduce_only if reduce_only else None,
                    new_client_order_id=client_id,
                )
                self._log("order", symbol, f"MARKET {side} qty={qty:.6f}")
                # Market fills immediately — set bracket
                self._on_fill(symbol, side, qty, config)

            elif mode == "IOC_LIMIT":
                price = self._get_ioc_price(symbol, side, config.ioc_epsilon)
                if price <= 0:
                    # Fallback to MARKET
                    binance_client.new_order(
                        symbol=symbol, side=side, order_type="MARKET",
                        quantity=qty, reduce_only=reduce_only if reduce_only else None,
                        new_client_order_id=client_id,
                    )
                    self._log("order", symbol, f"IOC fallback→MARKET {side} qty={qty:.6f}")
                    self._on_fill(symbol, side, qty, config)
                else:
                    price = ExchangeInfoCache.round_price(symbol, price)
                    result = binance_client.new_order(
                        symbol=symbol, side=side, order_type="LIMIT",
                        quantity=qty, price=price, time_in_force="IOC",
                        reduce_only=reduce_only if reduce_only else None,
                        new_client_order_id=client_id,
                    )
                    filled = float(result.get("executedQty", 0) or 0)
                    if filled > 0:
                        self._log("order", symbol, f"IOC {side} filled={filled:.6f}/{qty:.6f} @ {price}")
                        self._on_fill(symbol, side, filled, config)
                    else:
                        self._log("order", symbol, f"IOC {side} unfilled @ {price}")

            elif mode == "POST_ONLY_LIMIT":
                price = self._get_limit_price(symbol, side)
                if price <= 0:
                    return
                price = ExchangeInfoCache.round_price(symbol, price)
                binance_client.new_order(
                    symbol=symbol, side=side, order_type="LIMIT",
                    quantity=qty, price=price, time_in_force="GTX",
                    reduce_only=reduce_only if reduce_only else None,
                    new_client_order_id=client_id,
                )
                state.pending_order_id = client_id
                state.pending_side = side
                state.pending_qty = qty
                state.pending_placed_at = time.time()
                self._log("order", symbol, f"POST_ONLY {side} qty={qty:.6f} @ {price}")

        except Exception as exc:
            self._log("error", symbol, f"Order failed: {exc}")

    def _close_position(self, symbol: str, current: dict[str, Any], state: OrderState) -> None:
        """Close an entire position via MARKET order."""
        close_side = "SELL" if current["side"] == "LONG" else "BUY"
        qty = ExchangeInfoCache.round_quantity(symbol, current["qty"])
        try:
            # Cancel existing brackets
            self._cancel_brackets(symbol, state)
            binance_client.new_order(
                symbol=symbol,
                side=close_side,
                order_type="MARKET",
                quantity=qty,
                reduce_only=True,
            )
            self._log("exit", symbol, f"Close {current['side']} qty={qty:.6f} @ MARKET")
        except Exception as exc:
            self._log("error", symbol, f"Close failed: {exc}")

    # ── Fill handling & bracket management ───────────────────────

    def _on_fill(self, symbol: str, side: str, qty: float, config: EngineConfig) -> None:
        """After a fill, set SL/TP brackets."""
        state = self._get_state(symbol)
        try:
            # Fetch actual entry price from position
            positions = binance_client.position_information(symbol=symbol)
            entry_price = 0.0
            for p in positions:
                if float(p.get("positionAmt", 0)) != 0:
                    entry_price = float(p.get("entryPrice", 0))
                    break
            if entry_price <= 0:
                return

            state.last_bracket_entry_price = entry_price

            # Compute ATR for stop distance
            from app.engine.datafeed import fetch_atr_map
            atr_map = fetch_atr_map([symbol], config.signal_tf, config.stop_atr_window)
            atr = atr_map.get(symbol, entry_price * 0.02)  # fallback 2%

            stop_dist = atr * config.stop_k
            stop_side = "SELL" if side == "BUY" else "BUY"

            if side == "BUY":
                sl_price = entry_price - stop_dist
                tp_price = entry_price + stop_dist * 1.5  # TP = 1.5x SL distance
            else:
                sl_price = entry_price + stop_dist
                tp_price = entry_price - stop_dist * 1.5

            sl_price = ExchangeInfoCache.round_price(symbol, sl_price)
            tp_price = ExchangeInfoCache.round_price(symbol, tp_price)

            # Cancel old brackets
            self._cancel_brackets(symbol, state)

            # Place SL
            sl_id = f"eng_sl_{uuid.uuid4().hex[:8]}"
            try:
                binance_client.new_algo_order_close_position(
                    symbol=symbol, side=stop_side, order_type="STOP_MARKET",
                    trigger_price=sl_price, client_algo_id=sl_id,
                )
                state.active_sl_id = sl_id
            except Exception as e1:
                # Fallback: try with quantity
                try:
                    pos_qty = ExchangeInfoCache.round_quantity(symbol, qty)
                    binance_client.new_algo_order(
                        symbol=symbol, side=stop_side, order_type="STOP_MARKET",
                        trigger_price=sl_price, quantity=pos_qty, reduce_only=True,
                        client_algo_id=sl_id,
                    )
                    state.active_sl_id = sl_id
                except Exception as e2:
                    self._log("error", symbol, f"SL failed: {e2}")

            # Place TP
            tp_id = f"eng_tp_{uuid.uuid4().hex[:8]}"
            try:
                binance_client.new_algo_order_close_position(
                    symbol=symbol, side=stop_side, order_type="TAKE_PROFIT_MARKET",
                    trigger_price=tp_price, client_algo_id=tp_id,
                )
                state.active_tp_id = tp_id
            except Exception as e1:
                try:
                    pos_qty = ExchangeInfoCache.round_quantity(symbol, qty)
                    binance_client.new_algo_order(
                        symbol=symbol, side=stop_side, order_type="TAKE_PROFIT_MARKET",
                        trigger_price=tp_price, quantity=pos_qty, reduce_only=True,
                        client_algo_id=tp_id,
                    )
                    state.active_tp_id = tp_id
                except Exception as e2:
                    self._log("error", symbol, f"TP failed: {e2}")

            self._log("bracket", symbol,
                       f"SL={sl_price:.2f} TP={tp_price:.2f} (entry={entry_price:.2f}, ATR={atr:.2f})")

        except Exception as exc:
            self._log("error", symbol, f"Bracket setup failed: {exc}")

    def _cancel_brackets(self, symbol: str, state: OrderState) -> None:
        """Cancel existing SL/TP algo orders for a symbol."""
        try:
            binance_client.cancel_all_algo_orders(symbol=symbol)
        except Exception:
            pass
        state.active_sl_id = None
        state.active_tp_id = None

    def _cancel_stale_orders(self, state: OrderState, config: EngineConfig) -> None:
        """Cancel pending limit orders that are too old."""
        if not state.pending_order_id:
            return
        elapsed = time.time() - state.pending_placed_at
        if elapsed > STALE_ORDER_SEC:
            try:
                binance_client.cancel_order(
                    symbol=state.symbol,
                    client_order_id=state.pending_order_id,
                )
            except Exception:
                pass
            self._log("system", state.symbol,
                       f"Stale order cancelled after {elapsed:.0f}s")
            state.pending_order_id = None
            state.pending_side = None
            state.pending_qty = 0.0

    # ── Helpers ──────────────────────────────────────────────────

    def _get_price(self, symbol: str) -> float:
        """Get approximate current price."""
        try:
            bt = binance_client.book_ticker(symbol)
            bid = float(bt.get("bidPrice", 0) or 0)
            ask = float(bt.get("askPrice", 0) or 0)
            return (bid + ask) / 2 if bid > 0 and ask > 0 else 0.0
        except Exception:
            return 0.0

    def _get_ioc_price(self, symbol: str, side: str, epsilon: float) -> float:
        """Compute IOC limit price: mid ± epsilon."""
        try:
            bt = binance_client.book_ticker(symbol)
            bid = float(bt.get("bidPrice", 0) or 0)
            ask = float(bt.get("askPrice", 0) or 0)
            mid = (bid + ask) / 2
            if mid <= 0:
                return 0.0
            if side == "BUY":
                return mid * (1 + epsilon)  # slightly above mid
            else:
                return mid * (1 - epsilon)  # slightly below mid
        except Exception:
            return 0.0

    def _get_limit_price(self, symbol: str, side: str) -> float:
        """Get post-only limit price (at best bid/ask)."""
        try:
            bt = binance_client.book_ticker(symbol)
            if side == "BUY":
                return float(bt.get("bidPrice", 0) or 0)
            else:
                return float(bt.get("askPrice", 0) or 0)
        except Exception:
            return 0.0

    def _compute_slices(self, qty: float, symbol: str) -> list[float]:
        """Split large quantity into smaller slices."""
        filters = ExchangeInfoCache.get_symbol_filters(symbol)
        min_notional = filters.get("min_notional", 5.0)
        price = self._get_price(symbol)
        if price <= 0:
            return [qty]

        notional = qty * price
        if notional < min_notional * 3:
            return [qty]  # too small to slice

        n_slices = min(MAX_SLICES, max(1, int(notional / (min_notional * 10))))
        if n_slices <= 1:
            return [qty]

        slice_qty = qty / n_slices
        slice_qty = ExchangeInfoCache.round_quantity(symbol, slice_qty)
        if slice_qty <= 0:
            return [qty]

        slices = [slice_qty] * (n_slices - 1)
        remainder = qty - slice_qty * (n_slices - 1)
        remainder = ExchangeInfoCache.round_quantity(symbol, remainder)
        if remainder > 0:
            slices.append(remainder)
        return slices

    def reset(self) -> None:
        """Reset all states (e.g. on engine stop)."""
        self._states.clear()
        self._activity.clear()
