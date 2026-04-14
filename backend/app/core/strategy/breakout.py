"""15m EMA crossover trend-following strategy.

Enters LONG when EMA(fast) > EMA(slow) and price is above EMA(fast),
SHORT when EMA(fast) < EMA(slow) and price is below EMA(fast).
Filtered by overextension guard and minimum volume ratio.
"""

from typing import Any, Optional

from app.domain.enums import Side
from app.domain.signal import Signal


def _ema(values: list[float], period: int) -> list[float]:
    """Exponential moving average.  Pads beginning with 0.0."""
    if not values or period <= 0:
        return []
    result: list[float] = []
    k = 2.0 / (period + 1)
    for i, v in enumerate(values):
        if i < period - 1:
            result.append(0.0)
        elif i == period - 1:
            result.append(sum(values[: period]) / period)
        else:
            result.append(v * k + result[-1] * (1 - k))
    return result


def _atr(candles: list[dict], period: int = 14) -> list[float]:
    """Average True Range."""
    if len(candles) < period + 1:
        return []
    tr_list: list[float] = [0.0]
    for i in range(1, len(candles)):
        high = candles[i]["high"]
        low = candles[i]["low"]
        prev_close = candles[i - 1]["close"]
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        tr_list.append(tr)
    atr_list: list[float] = []
    for i in range(len(candles)):
        if i < period:
            atr_list.append(0.0)
        else:
            atr_list.append(sum(tr_list[i - period + 1 : i + 1]) / period)
    return atr_list


def _average_volume(candles: list[dict], period: int = 20) -> Optional[float]:
    """N-period average volume."""
    if len(candles) < period:
        return None
    vols = [c["volume"] for c in candles[-period:]]
    return sum(vols) / period


def _last_n_candles_movement(candles: list[dict], n: int) -> float:
    """Total absolute movement over last n candles (high-low range sum)."""
    if len(candles) < n:
        return 0.0
    total = 0.0
    for c in candles[-n:]:
        total += c["high"] - c["low"]
    return total


class BreakoutStrategy:
    """15m EMA crossover trend-following strategy with overextension filter."""

    EMA_FAST_PERIOD = 12
    EMA_SLOW_PERIOD = 50
    VOLUME_LOOKBACK = 20
    VOLUME_RATIO_THRESHOLD = 1.0
    OVEREXTENSION_LOOKBACK = 10
    ATR_PERIOD = 14
    ATR_OVEREXTENSION = 2.5
    SL_ATR_MULTIPLIER = 1.6
    TP_ATR_MULTIPLIER = 2.8  # R:R ≈ 1:1.75 (balanced for BTC 15m volatility)

    def __init__(self, symbol: str = "BTCUSDT") -> None:
        self._symbol = symbol
        self._last_evaluation: dict = {}

    def get_last_evaluation_details(self) -> dict:
        """Return last evaluation context for insight API."""
        return dict(self._last_evaluation)

    def _store_evaluation(
        self,
        atr_value: float,
        volume_ratio: float,
        regime: str,
        engine_reasoning: list[str],
        close_price: float = 0.0,
        *,
        ema_fast: float = 0.0,
        ema_slow: float = 0.0,
        entry_conditions: Optional[list[dict[str, Any]]] = None,
    ) -> None:
        """Store last evaluation for insight API."""
        vol_score = (atr_value / close_price) * 100.0 if close_price > 0 else 0.0
        self._last_evaluation = {
            "regime": regime,
            "volatility_score": round(vol_score, 4),
            "atr_value": round(atr_value, 4),
            "volume_ratio": round(volume_ratio, 4),
            "engine_reasoning": engine_reasoning,
            "ema_fast": round(ema_fast, 4),
            "ema_slow": round(ema_slow, 4),
        }
        if entry_conditions is not None:
            self._last_evaluation["entry_conditions"] = entry_conditions

    def _compute_core(
        self, candles: list[dict], close: float,
        trend_direction: str = "neutral",
    ) -> Optional[dict]:
        """Shared computation for evaluate() and update_insight_live()."""
        closes = [c["close"] for c in candles]

        ema_f = _ema(closes, self.EMA_FAST_PERIOD)
        ema_s = _ema(closes, self.EMA_SLOW_PERIOD)
        if not ema_f or not ema_s or ema_f[-1] == 0.0 or ema_s[-1] == 0.0:
            return None

        atr_vals = _atr(candles, self.ATR_PERIOD)
        if len(atr_vals) != len(candles):
            return None
        current_atr = atr_vals[-1]

        current_vol = candles[-1]["volume"]
        avg_vol = _average_volume(candles, self.VOLUME_LOOKBACK)
        volume_ratio = current_vol / avg_vol if avg_vol and avg_vol > 0 else 0.0

        movement = _last_n_candles_movement(candles, self.OVEREXTENSION_LOOKBACK)
        overext_threshold = self.ATR_OVEREXTENSION * current_atr * self.OVEREXTENSION_LOOKBACK if current_atr > 0 else float("inf")
        overextended = movement >= overext_threshold

        ema_cross_long = ema_f[-1] > ema_s[-1]
        price_above_fast = close > ema_f[-1]
        price_below_fast = close < ema_f[-1]
        vol_ok = volume_ratio >= self.VOLUME_RATIO_THRESHOLD

        if trend_direction == "up":
            signal_side = "LONG"
        elif trend_direction == "down":
            signal_side = "SHORT"
        else:
            signal_side = "LONG" if ema_cross_long else "SHORT"

        is_long = signal_side == "LONG"
        ema_aligned = ema_cross_long if is_long else not ema_cross_long
        price_aligned = price_above_fast if is_long else price_below_fast

        long_signal = is_long and ema_aligned and price_aligned and not overextended and vol_ok
        short_signal = (not is_long) and ema_aligned and price_aligned and not overextended and vol_ok

        entry_conds = [
            {
                "label": f"Trend direction ({signal_side})",
                "required": f"HH/LL → {'up' if is_long else 'down'}",
                "actual": trend_direction,
                "met": (trend_direction == "up") if is_long else (trend_direction == "down"),
            },
            {
                "label": f"EMA Cross ({signal_side})",
                "required": (f"EMA({self.EMA_FAST_PERIOD}) > EMA({self.EMA_SLOW_PERIOD})"
                             if is_long
                             else f"EMA({self.EMA_FAST_PERIOD}) < EMA({self.EMA_SLOW_PERIOD})"),
                "actual": f"{ema_f[-1]:,.2f} vs {ema_s[-1]:,.2f}",
                "met": ema_aligned,
            },
            {
                "label": f"Price {'above' if is_long else 'below'} EMA({self.EMA_FAST_PERIOD})",
                "required": f"{'>' if is_long else '<'} {ema_f[-1]:,.2f}",
                "actual": f"{close:,.2f}",
                "met": price_aligned,
            },
            {
                "label": "Not overextended",
                "required": f"movement < {overext_threshold:.1f}",
                "actual": f"{movement:.2f}",
                "met": not overextended,
            },
            {
                "label": "Volume ratio",
                "required": f">= {self.VOLUME_RATIO_THRESHOLD}",
                "actual": f"{volume_ratio:.2f}",
                "met": vol_ok,
            },
        ]

        return {
            "ema_fast_val": ema_f[-1],
            "ema_slow_val": ema_s[-1],
            "current_atr": current_atr,
            "volume_ratio": volume_ratio,
            "overextended": overextended,
            "overext_threshold": overext_threshold,
            "movement": movement,
            "long_signal": long_signal,
            "short_signal": short_signal,
            "signal_side": signal_side,
            "entry_conds": entry_conds,
        }

    def update_insight_live(
        self, candles: list[dict], live_bar: dict,
        trend_direction: str = "neutral",
    ) -> None:
        """Refresh ``_last_evaluation`` using the forming bar without producing signals."""
        required_keys = ("open", "high", "low", "close", "volume")
        if not candles or not all(
            isinstance(c, dict) and all(k in c for k in required_keys)
            for c in candles
        ):
            return
        minimum_required = max(self.EMA_SLOW_PERIOD + 1, self.ATR_PERIOD + 1, self.VOLUME_LOOKBACK + 1)
        if len(candles) < minimum_required:
            return

        close = live_bar["close"]
        ctx = self._compute_core(candles, close, trend_direction)
        if ctx is None:
            return

        reasoning = [f"EMA({self.EMA_FAST_PERIOD})={ctx['ema_fast_val']:,.2f}  EMA({self.EMA_SLOW_PERIOD})={ctx['ema_slow_val']:,.2f}"]
        reasoning.append(f"Live: close {close:,.2f}, vol ratio {ctx['volume_ratio']:.2f}")
        if ctx["long_signal"]:
            reasoning.append("EMA bullish cross + price above fast EMA")
        elif ctx["short_signal"]:
            reasoning.append("EMA bearish cross + price below fast EMA")

        regime = "Trending" if ctx["long_signal"] or ctx["short_signal"] else "Ranging"
        if ctx["overextended"]:
            regime = "High Volatility"

        self._store_evaluation(
            ctx["current_atr"], ctx["volume_ratio"], regime, reasoning,
            close, ema_fast=ctx["ema_fast_val"], ema_slow=ctx["ema_slow_val"],
            entry_conditions=ctx["entry_conds"],
        )

    def evaluate(
        self, candles: list[dict],
        trend_direction: str = "neutral",
    ) -> Optional[Signal]:
        """Evaluate closed candles using EMA crossover. Returns Signal or None."""
        required_keys = ("open", "high", "low", "close", "volume")
        if not candles or not all(
            isinstance(c, dict) and all(k in c for k in required_keys)
            for c in candles
        ):
            return None
        minimum_required = max(self.EMA_SLOW_PERIOD + 1, self.ATR_PERIOD + 1, self.VOLUME_LOOKBACK + 1)
        if len(candles) < minimum_required:
            return None

        cur = candles[-1]
        close = cur["close"]
        signal_time = cur.get("timestamp", "")

        ctx = self._compute_core(candles, close, trend_direction)
        if ctx is None:
            return None

        reasoning: list[str] = [
            f"EMA({self.EMA_FAST_PERIOD})={ctx['ema_fast_val']:,.2f}  EMA({self.EMA_SLOW_PERIOD})={ctx['ema_slow_val']:,.2f}"
        ]

        if ctx["overextended"]:
            reasoning.append(
                f"Movement {ctx['movement']:.2f} >= overextension {ctx['overext_threshold']:.2f} → filtered"
            )
            self._store_evaluation(
                ctx["current_atr"], ctx["volume_ratio"], "High Volatility", reasoning,
                close, ema_fast=ctx["ema_fast_val"], ema_slow=ctx["ema_slow_val"],
                entry_conditions=ctx["entry_conds"],
            )
            return None

        if ctx["long_signal"]:
            atr = ctx["current_atr"]
            sl_price = close - self.SL_ATR_MULTIPLIER * atr
            tp_price = close + self.TP_ATR_MULTIPLIER * atr
            reasoning.append(f"LONG: EMA bullish cross, close {close:,.2f} > EMA({self.EMA_FAST_PERIOD}) {ctx['ema_fast_val']:,.2f}, vol ratio {ctx['volume_ratio']:.2f}")
            reasoning.append(f"SL={sl_price:,.2f} (ATR×{self.SL_ATR_MULTIPLIER}), TP={tp_price:,.2f} (ATR×{self.TP_ATR_MULTIPLIER})")
            self._store_evaluation(
                ctx["current_atr"], ctx["volume_ratio"], "Trending", reasoning,
                close, ema_fast=ctx["ema_fast_val"], ema_slow=ctx["ema_slow_val"],
                entry_conditions=ctx["entry_conds"],
            )
            return Signal(
                symbol=self._symbol, side=Side.LONG,
                signal_time=signal_time, signal_candle_ts=signal_time,
                stop_price=sl_price, take_profit=tp_price,
            )

        if ctx["short_signal"]:
            atr = ctx["current_atr"]
            sl_price = close + self.SL_ATR_MULTIPLIER * atr
            tp_price = close - self.TP_ATR_MULTIPLIER * atr
            reasoning.append(f"SHORT: EMA bearish cross, close {close:,.2f} < EMA({self.EMA_FAST_PERIOD}) {ctx['ema_fast_val']:,.2f}, vol ratio {ctx['volume_ratio']:.2f}")
            reasoning.append(f"SL={sl_price:,.2f} (ATR×{self.SL_ATR_MULTIPLIER}), TP={tp_price:,.2f} (ATR×{self.TP_ATR_MULTIPLIER})")
            self._store_evaluation(
                ctx["current_atr"], ctx["volume_ratio"], "Trending", reasoning,
                close, ema_fast=ctx["ema_fast_val"], ema_slow=ctx["ema_slow_val"],
                entry_conditions=ctx["entry_conds"],
            )
            return Signal(
                symbol=self._symbol, side=Side.SHORT,
                signal_time=signal_time, signal_candle_ts=signal_time,
                stop_price=sl_price, take_profit=tp_price,
            )

        if ctx["volume_ratio"] < self.VOLUME_RATIO_THRESHOLD:
            reasoning.append(f"Volume ratio {ctx['volume_ratio']:.2f} < {self.VOLUME_RATIO_THRESHOLD} → insufficient volume")
        else:
            reasoning.append("No EMA cross alignment with price")

        self._store_evaluation(
            ctx["current_atr"], ctx["volume_ratio"], "Ranging", reasoning,
            close, ema_fast=ctx["ema_fast_val"], ema_slow=ctx["ema_slow_val"],
            entry_conditions=ctx["entry_conds"],
        )
        return None
