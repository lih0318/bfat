"""Regime-switching strategy engine.

Delegates to BreakoutStrategy (TRENDING) or RangeStrategy (RANGING)
based on RegimeClassifier output.

Features:
  * Regime-switch PnL gate: losing → close, winning → hold + cooldown
  * Strong-momentum conditional immediate entry after regime switch
  * Score-based position sizing (0.7x / 1.0x / 1.2x)
  * Cooldown after soft regime transitions (2 candles)
"""

from typing import Any, Optional, Union

from app.core.strategy.breakout import BreakoutStrategy
from app.domain.enums import Side
from app.domain.regime_classifier import RegimeClassifier, _adx
from app.domain.signal import CloseSignal, Signal
from app.domain.strategies.range_strategy import RangeStrategy

_MOMENTUM_ADX_THRESHOLD = 25
_MOMENTUM_VOL_Z_THRESHOLD = 1.0
_MOMENTUM_BODY_RATIO_THRESHOLD = 0.6
_MOMENTUM_MIN_CONDITIONS = 2

_COOLDOWN_BARS = 2


def _volume_zscore(candles: list[dict], period: int = 20) -> Optional[float]:
    """Z-score of current candle volume vs past-only rolling window."""
    if len(candles) < period + 1:
        return None
    window = [c["volume"] for c in candles[-(period + 1) : -1]]
    mean = sum(window) / len(window)
    variance = sum((v - mean) ** 2 for v in window) / len(window)
    std = variance**0.5 if variance > 0 else 0.0
    if std == 0:
        return None
    return (candles[-1]["volume"] - mean) / std


def _position_scale_for_score(score: int) -> float:
    if score >= 3:
        return 1.2
    if score >= 2:
        return 1.0
    return 0.7


class StrategyEngine:
    """Single entry point for strategy evaluation with regime switching.

    * Only ONE strategy is active at a time.
    * Regime change with losing position → CloseSignal.
    * Regime change with winning position → hold + cooldown (no strategy eval).
    * Strong-momentum bypass for immediate entry after regime switch.
    * Score-based position sizing applied to every signal.
    """

    def __init__(self, symbol: str = "BTCUSDT") -> None:
        self.regime_classifier = RegimeClassifier()
        self.breakout_strategy = BreakoutStrategy(symbol)
        self.range_strategy = RangeStrategy(symbol)
        self._active_regime: Optional[str] = None
        self._last_regime_changed: bool = False
        self._regime_switch_cooldown: int = 0
        self._last_score: int = 0
        self._last_position_scale: float = 1.0

    # ── Public API ─────────────────────────────────────────────────

    def evaluate(
        self,
        candles: list[dict],
        current_position: Any,
    ) -> Optional[Union[Signal, CloseSignal]]:
        """Evaluate candles under the current regime.

        Returns:
            Signal       – new entry (LONG or SHORT, with position_scale)
            CloseSignal  – close existing position (regime switch, losing)
            None         – no action (cooldown, no signal, winning hold)
        """
        regime = self.regime_classifier.evaluate(candles)
        rc_details = self.regime_classifier.get_last_details()
        score = rc_details.get("score", 0)

        regime_changed = (
            self._active_regime is not None and regime != self._active_regime
        )
        self._last_regime_changed = regime_changed
        self._last_score = score
        position_scale = _position_scale_for_score(score)
        self._last_position_scale = position_scale

        # ── 1. Regime-switch handling ──────────────────────────────
        if regime_changed:
            self._active_regime = regime

            if current_position is not None:
                unrealized = self._unrealized_pnl(current_position, candles)
                if unrealized < 0:
                    return CloseSignal(reason="Regime Switch - Losing Position")
                self._regime_switch_cooldown = _COOLDOWN_BARS
                return None

            if not self._check_strong_momentum(candles):
                self._regime_switch_cooldown = _COOLDOWN_BARS
                return None
        else:
            self._active_regime = regime

        # ── 2. Cooldown gate ──────────────────────────────────────
        if self._regime_switch_cooldown > 0:
            self._regime_switch_cooldown -= 1
            return None

        # ── 3. Active strategy evaluation ─────────────────────────
        signal = self._evaluate_active_strategy(candles)

        # ── 4. Apply position scale ───────────────────────────────
        if signal is not None:
            signal = Signal(
                symbol=signal.symbol,
                side=signal.side,
                signal_time=signal.signal_time,
                signal_candle_ts=signal.signal_candle_ts,
                stop_price=signal.stop_price,
                take_profit=signal.take_profit,
                position_scale=position_scale,
            )
        return signal

    def evaluate_for_insight(self, candles: list[dict]) -> None:
        """Insight seeding only — no position awareness, no CloseSignal."""
        regime = self.regime_classifier.evaluate(candles)
        rc_details = self.regime_classifier.get_last_details()
        self._active_regime = regime
        self._last_score = rc_details.get("score", 0)
        self._last_position_scale = _position_scale_for_score(self._last_score)
        self._evaluate_active_strategy(candles)

    def get_last_evaluation_details(self) -> dict:
        """Merged insight: active strategy details + regime + sizing."""
        if self._active_regime == "TRENDING":
            details = dict(self.breakout_strategy.get_last_evaluation_details())
        elif self._active_regime == "RANGING":
            details = dict(self.range_strategy.get_last_evaluation_details())
        else:
            details = {}

        details["regime"] = self._active_regime or "Unknown"
        details["active_strategy"] = (
            "Breakout" if self._active_regime == "TRENDING" else "Range"
        )
        details["regime_changed"] = self._last_regime_changed
        details["regime_score"] = self._last_score
        details["position_scale"] = self._last_position_scale
        details["cooldown_remaining"] = self._regime_switch_cooldown
        details["regime_classifier"] = self.regime_classifier.get_last_details()
        return details

    # ── Internal helpers ───────────────────────────────────────────

    def _evaluate_active_strategy(
        self, candles: list[dict]
    ) -> Optional[Signal]:
        if self._active_regime == "TRENDING":
            return self.breakout_strategy.evaluate(candles)
        if self._active_regime == "RANGING":
            return self.range_strategy.evaluate(candles)
        return None

    @staticmethod
    def _unrealized_pnl(position: Any, candles: list[dict]) -> float:
        """Compute unrealized PnL from position entry vs current close."""
        close = candles[-1]["close"]
        if position.side == Side.LONG:
            return (close - position.entry_price) * position.size
        return (position.entry_price - close) * position.size

    @staticmethod
    def _check_strong_momentum(candles: list[dict]) -> bool:
        """At least 2 of 3 momentum conditions met → allow immediate entry.

        Uses raw _adx() directly on candles (no hysteresis dependency).
        """
        met = 0
        adx = _adx(candles)
        if adx is not None and adx > _MOMENTUM_ADX_THRESHOLD:
            met += 1
        vol_z = _volume_zscore(candles)
        if vol_z is not None and vol_z > _MOMENTUM_VOL_Z_THRESHOLD:
            met += 1
        cur = candles[-1]
        body = abs(cur["close"] - cur["open"])
        hl_range = cur["high"] - cur["low"]
        if hl_range > 0 and (body / hl_range) > _MOMENTUM_BODY_RATIO_THRESHOLD:
            met += 1
        return met >= _MOMENTUM_MIN_CONDITIONS
