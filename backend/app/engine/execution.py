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
    """Per-symbol order tracking with partial TP state machine."""
    symbol: str
    pending_order_id: Optional[str] = None
    pending_side: Optional[str] = None
    pending_qty: float = 0.0
    pending_placed_at: float = 0.0
    filled_qty: float = 0.0
    active_sl_id: Optional[str] = None
    active_tp1_id: Optional[str] = None
    active_tp2_id: Optional[str] = None
    last_bracket_entry_price: float = 0.0
    # Chandelier / partial TP state
    initial_qty: float = 0.0
    initial_r: float = 0.0  # SL distance in price units (1R)
    position_side: Optional[str] = None  # "BUY" or "SELL"
    tp1_done: bool = False
    tp2_done: bool = False
    sl_price: float = 0.0
    tp1_price: float = 0.0
    tp2_price: float = 0.0
    be_moved: bool = False


class ExecutionEngine:
    """Manages order placement and bracket (SL/TP) lifecycle."""

    def __init__(self) -> None:
        self._states: dict[str, OrderState] = {}
        self._activity: list[dict[str, Any]] = []
        self._max_activity = 200

    @property
    def activity_log(self) -> list[dict[str, Any]]:
        return list(self._activity)

    # Event types that should also be forwarded to the accounting ledger
    _LEDGER_EVENT_TYPES = frozenset({
        "tp1_filled", "tp2_filled", "sl_moved_to_breakeven",
        "sl_triggered", "chandelier_sl_updated",
    })

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

        # Forward certain events to accounting ledger for Activity/Insight visibility
        if typ in self._LEDGER_EVENT_TYPES:
            try:
                from app.engine.accounting import ledger
                ledger.record(typ, {"symbol": symbol, "message": msg, **extra})
            except Exception:
                pass

    def _get_state(self, symbol: str) -> OrderState:
        if symbol not in self._states:
            self._states[symbol] = OrderState(symbol=symbol)
        return self._states[symbol]

    # ── Pre-flight ────────────────────────────────────────────────

    def _preflight(
        self,
        symbol: str,
        config: EngineConfig,
        computed_leverage: int = 0,
    ) -> tuple[bool, str]:
        """
        Pre-flight check before placing an order for a symbol:
        1. Set margin mode (ISOLATED/CROSSED)
        2. Set leverage (from risk-sizing computed_leverage, or config fallback)
        Returns (ok, reason).
        """
        target_margin = config.margin_mode  # "ISOLATED" or "CROSSED"
        # Use per-symbol computed leverage if provided, otherwise fallback
        if computed_leverage > 0:
            leverage = max(
                config.min_symbol_leverage,
                min(config.max_symbol_leverage, computed_leverage),
            )
        else:
            leverage = max(
                config.min_symbol_leverage,
                min(config.max_symbol_leverage, int(config.effective_leverage_target * 2)),
            )

        # 1. Margin mode switch
        try:
            binance_client.set_margin_type(symbol, target_margin)
        except Exception as exc:
            reason = f"margin_mode_switch_failed: {exc}"
            self._log("preflight_fail", symbol, reason)
            return False, "margin_mode_switch_failed"

        # 2. Leverage setting
        try:
            binance_client.set_leverage(symbol=symbol, leverage=leverage)
        except Exception as exc:
            # Non-fatal: log but proceed (Binance may already be at correct leverage)
            self._log("preflight_warn", symbol, f"leverage set warning: {exc}")

        self._log("preflight_ok", symbol,
                  f"margin={target_margin}, leverage={leverage}x")
        return True, ""

    # ── Main tick ────────────────────────────────────────────────

    def tick(
        self,
        targets: dict[str, TargetPosition],
        config: EngineConfig,
        equity: float,
    ) -> Optional[dict[str, Any]]:
        """
        Single execution tick:
        0. Pre-flight: margin mode + leverage per symbol
        1. Fetch current positions
        2. Compute deltas
        3. Cancel stale orders
        4. Place new orders for significant deltas
        5. Manage brackets

        Returns summary dict: { orders_placed, skip_reasons, targets_count } for logging.
        """
        # Fetch all positions
        try:
            all_positions = binance_client.position_information()
        except Exception as exc:
            logger.error("execution tick: position fetch failed: %s", exc)
            return None

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
        orders_placed = 0
        skip_reasons: list[dict[str, str]] = []

        for sym, target in targets.items():
            # Pre-flight: margin mode + leverage
            pf_ok, pf_reason = self._preflight(sym, config, computed_leverage=target.computed_leverage)
            if not pf_ok:
                skip_reasons.append({"symbol": sym, "reason": pf_reason})
                continue

            state = self._get_state(sym)
            current = current_map.get(sym)

            # Cancel stale pending orders
            self._cancel_stale_orders(state, config)

            if state.pending_order_id:
                skip_reasons.append({"symbol": sym, "reason": "pending_order"})
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
                skip_reasons.append({"symbol": sym, "reason": "delta_below_threshold"})
                continue

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
                    skip_reasons.append({"symbol": sym, "reason": "remaining_below_threshold"})
                    continue

            # Round qty
            order_qty = ExchangeInfoCache.round_quantity(sym, order_qty)
            if order_qty <= 0:
                skip_reasons.append({"symbol": sym, "reason": "rounded_qty_zero"})
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
            orders_placed += len(slices)

            for slice_qty in slices:
                self._place_order(sym, order_side, slice_qty, config, state, reduce_only)
                if len(slices) > 1:
                    time.sleep(0.3)  # brief pause between slices

        # Close positions for symbols NOT in targets
        for sym, current in current_map.items():
            if sym not in targets:
                state = self._get_state(sym)
                self._close_position(sym, current, state)
                orders_placed += 1  # close counts as an order

        return {
            "orders_placed": orders_placed,
            "skip_reasons": skip_reasons,
            "targets_count": len(targets),
        }

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
        """After an entry fill, set Chandelier SL + TP1/TP2 partial brackets."""
        state = self._get_state(symbol)
        try:
            # Fetch actual position data
            positions = binance_client.position_information(symbol=symbol)
            entry_price = 0.0
            actual_qty = 0.0
            actual_side = side
            for p in positions:
                amt = float(p.get("positionAmt", 0))
                if amt != 0:
                    entry_price = float(p.get("entryPrice", 0))
                    actual_qty = abs(amt)
                    actual_side = "BUY" if amt > 0 else "SELL"
                    break

            if entry_price <= 0 or actual_qty <= 0:
                self._log("bracket_skip", symbol, "No open position found after fill")
                return

            state.last_bracket_entry_price = entry_price
            state.position_side = actual_side
            state.initial_qty = actual_qty
            state.tp1_done = False
            state.tp2_done = False
            state.be_moved = False

            # Compute ATR for Chandelier stop
            from app.engine.datafeed import fetch_atr_map
            atr_map = fetch_atr_map([symbol], config.signal_tf, config.stop_atr_window)
            atr = atr_map.get(symbol, entry_price * 0.02)

            # Chandelier SL distance (1R)
            sl_dist = atr * config.chandelier_atr_mult
            state.initial_r = sl_dist
            stop_side = "SELL" if actual_side == "BUY" else "BUY"

            # Calculate prices
            if actual_side == "BUY":
                sl_price = entry_price - sl_dist
                tp1_price = entry_price + sl_dist * config.tp1_r_multiple
                tp2_price = entry_price + sl_dist * config.tp2_r_multiple
            else:
                sl_price = entry_price + sl_dist
                tp1_price = entry_price - sl_dist * config.tp1_r_multiple
                tp2_price = entry_price - sl_dist * config.tp2_r_multiple

            sl_price = ExchangeInfoCache.round_price(symbol, sl_price)
            tp1_price = ExchangeInfoCache.round_price(symbol, tp1_price)
            tp2_price = ExchangeInfoCache.round_price(symbol, tp2_price)
            state.sl_price = sl_price
            state.tp1_price = tp1_price
            state.tp2_price = tp2_price

            # Cancel old brackets
            self._cancel_brackets(symbol, state)

            # TP1 qty / TP2 qty
            tp1_qty = ExchangeInfoCache.round_quantity(symbol, actual_qty * config.tp1_close_pct)
            tp2_qty = ExchangeInfoCache.round_quantity(symbol, (actual_qty - tp1_qty) * config.tp2_close_pct)

            # Place SL (full position via closePosition)
            sl_id = f"eng_sl_{uuid.uuid4().hex[:8]}"
            try:
                binance_client.new_algo_order_close_position(
                    symbol=symbol, side=stop_side, order_type="STOP_MARKET",
                    trigger_price=sl_price, client_algo_id=sl_id,
                )
                state.active_sl_id = sl_id
            except Exception:
                try:
                    binance_client.new_algo_order(
                        symbol=symbol, side=stop_side, order_type="STOP_MARKET",
                        trigger_price=sl_price, quantity=actual_qty, reduce_only=True,
                        client_algo_id=sl_id,
                    )
                    state.active_sl_id = sl_id
                except Exception as e2:
                    self._log("error", symbol, f"SL failed: {e2}")

            # Place TP1 (partial qty)
            if tp1_qty > 0:
                tp1_id = f"eng_tp1_{uuid.uuid4().hex[:8]}"
                try:
                    binance_client.new_algo_order(
                        symbol=symbol, side=stop_side, order_type="TAKE_PROFIT_MARKET",
                        trigger_price=tp1_price, quantity=tp1_qty, reduce_only=True,
                        client_algo_id=tp1_id,
                    )
                    state.active_tp1_id = tp1_id
                except Exception as e:
                    self._log("error", symbol, f"TP1 failed: {e}")

            # Place TP2 (remaining qty)
            if tp2_qty > 0:
                tp2_id = f"eng_tp2_{uuid.uuid4().hex[:8]}"
                try:
                    binance_client.new_algo_order(
                        symbol=symbol, side=stop_side, order_type="TAKE_PROFIT_MARKET",
                        trigger_price=tp2_price, quantity=tp2_qty, reduce_only=True,
                        client_algo_id=tp2_id,
                    )
                    state.active_tp2_id = tp2_id
                except Exception as e:
                    self._log("error", symbol, f"TP2 failed: {e}")

            self._log("bracket", symbol,
                       f"Chandelier SL={sl_price:.2f} TP1={tp1_price:.2f}({tp1_qty:.6f}) "
                       f"TP2={tp2_price:.2f}({tp2_qty:.6f}) (entry={entry_price:.2f}, ATR={atr:.2f}, 1R={sl_dist:.2f})")

        except Exception as exc:
            self._log("error", symbol, f"Bracket setup failed: {exc}")

    def on_ws_fill(self, fill: dict[str, Any], config: EngineConfig) -> None:
        """Handle WebSocket fill event: detect entry fill (setup brackets) or TP1/TP2/SL fills."""
        symbol = fill.get("symbol", "")
        client_id = fill.get("client_id", "")
        if not symbol or not client_id:
            return

        state = self._get_state(symbol)

        # Entry fill: pending order filled via WebSocket → set up brackets
        if state.pending_order_id and client_id == state.pending_order_id:
            side = fill.get("side", "") or state.pending_side or ""
            filled_qty = float(fill.get("filled_qty", 0) or 0)
            if side and filled_qty > 0:
                try:
                    self._on_fill(symbol, side, filled_qty, config)
                except Exception as exc:
                    self._log("error", symbol, f"Bracket setup on WS fill failed: {exc}")
            state.pending_order_id = None
            state.pending_side = None
            state.pending_qty = 0.0
            state.pending_placed_at = 0.0
            return

        # Check if this fill is our TP1
        if state.active_tp1_id and client_id == state.active_tp1_id and not state.tp1_done:
            state.tp1_done = True
            self._log("tp1_filled", symbol,
                       f"TP1 filled @ {fill.get('avg_price', 0):.2f}")

            # Move SL to breakeven if configured
            if config.breakeven_after_tp1 and state.last_bracket_entry_price > 0:
                self._move_sl_to_breakeven(symbol, state, config)

        # Check if this fill is our TP2
        elif state.active_tp2_id and client_id == state.active_tp2_id and not state.tp2_done:
            state.tp2_done = True
            self._log("tp2_filled", symbol,
                       f"TP2 filled @ {fill.get('avg_price', 0):.2f}")
            # Cancel remaining SL since position should be fully closed
            self._cancel_brackets(symbol, state)

        # Check if this fill is our SL
        elif state.active_sl_id and client_id == state.active_sl_id:
            self._log("sl_triggered", symbol,
                       f"SL triggered @ {fill.get('avg_price', 0):.2f}")
            # Cancel remaining TP orders
            self._cancel_brackets(symbol, state)

    def _move_sl_to_breakeven(self, symbol: str, state: OrderState, config: EngineConfig) -> None:
        """After TP1 fill, cancel old SL and place new one at breakeven + offset."""
        entry = state.last_bracket_entry_price
        offset_pct = config.breakeven_offset_bps / 10000.0

        if state.position_side == "BUY":
            new_sl = entry * (1 + offset_pct)
            stop_side = "SELL"
        else:
            new_sl = entry * (1 - offset_pct)
            stop_side = "BUY"

        new_sl = ExchangeInfoCache.round_price(symbol, new_sl)

        # Cancel old SL
        if state.active_sl_id:
            try:
                binance_client.cancel_algo_order(client_algo_id=state.active_sl_id)
            except Exception:
                pass
            # Also cancel all algo orders as safety
            try:
                binance_client.cancel_all_algo_orders(symbol=symbol)
            except Exception:
                pass

        # Place new SL at breakeven
        sl_id = f"eng_slbe_{uuid.uuid4().hex[:8]}"
        try:
            binance_client.new_algo_order_close_position(
                symbol=symbol, side=stop_side, order_type="STOP_MARKET",
                trigger_price=new_sl, client_algo_id=sl_id,
            )
            state.active_sl_id = sl_id
        except Exception:
            try:
                # Fetch remaining qty
                positions = binance_client.position_information(symbol=symbol)
                remain_qty = 0.0
                for p in positions:
                    amt = float(p.get("positionAmt", 0))
                    if amt != 0:
                        remain_qty = abs(amt)
                        break
                if remain_qty > 0:
                    binance_client.new_algo_order(
                        symbol=symbol, side=stop_side, order_type="STOP_MARKET",
                        trigger_price=new_sl, quantity=remain_qty, reduce_only=True,
                        client_algo_id=sl_id,
                    )
                    state.active_sl_id = sl_id
            except Exception as e:
                self._log("error", symbol, f"BE SL move failed: {e}")

        # Re-place TP2 if it was cancelled along with the blanket cancel
        if state.active_tp2_id and not state.tp2_done and state.tp2_price > 0:
            tp2_id = f"eng_tp2_{uuid.uuid4().hex[:8]}"
            try:
                positions = binance_client.position_information(symbol=symbol)
                remain_qty = 0.0
                for p in positions:
                    amt = float(p.get("positionAmt", 0))
                    if amt != 0:
                        remain_qty = abs(amt)
                        break
                if remain_qty > 0:
                    remain_qty = ExchangeInfoCache.round_quantity(symbol, remain_qty)
                    binance_client.new_algo_order(
                        symbol=symbol, side=stop_side, order_type="TAKE_PROFIT_MARKET",
                        trigger_price=state.tp2_price, quantity=remain_qty, reduce_only=True,
                        client_algo_id=tp2_id,
                    )
                    state.active_tp2_id = tp2_id
            except Exception as e:
                self._log("error", symbol, f"TP2 re-place after BE failed: {e}")

        state.be_moved = True
        state.sl_price = new_sl
        self._log("sl_moved_to_breakeven", symbol,
                   f"SL moved to BE={new_sl:.2f} (entry={entry:.2f}, offset={config.breakeven_offset_bps}bps)")

    def _cancel_brackets(self, symbol: str, state: OrderState) -> None:
        """Cancel ALL existing SL/TP and open orders for a symbol.
        Ensures no stale bracket or OCO remnant survives a flip/adjust."""
        # 1. Cancel algo orders (SL/TP)
        try:
            binance_client.cancel_all_algo_orders(symbol=symbol)
        except Exception:
            pass
        # 2. Cancel regular open orders (e.g. limit SL/TP, OCO remnants)
        try:
            binance_client.cancel_all_open_orders(symbol=symbol)
        except Exception:
            pass
        state.active_sl_id = None
        state.active_tp1_id = None
        state.active_tp2_id = None

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

    def get_bracket_state(self, symbol: str) -> dict[str, Any]:
        """Return current bracket state for a symbol (for API/UI)."""
        state = self._states.get(symbol)
        if not state:
            return {}
        return {
            "sl_price": state.sl_price,
            "tp1_price": state.tp1_price,
            "tp2_price": state.tp2_price,
            "tp1_done": state.tp1_done,
            "tp2_done": state.tp2_done,
            "be_moved": state.be_moved,
            "entry_price": state.last_bracket_entry_price,
            "initial_r": state.initial_r,
            "position_side": state.position_side,
        }

    def get_all_bracket_states(self) -> dict[str, dict[str, Any]]:
        """Return bracket states for all tracked symbols."""
        result = {}
        for sym, state in self._states.items():
            if state.sl_price > 0 or state.tp1_price > 0:
                result[sym] = self.get_bracket_state(sym)
        return result

    def reset(self) -> None:
        """Reset all states (e.g. on engine stop)."""
        self._states.clear()
        self._activity.clear()
