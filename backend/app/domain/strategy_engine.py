"""Regime-switching strategy engine.

Delegates to BreakoutStrategy (TRENDING) or RangeStrategy (RANGING)
based on RegimeClassifier output.  Handles regime transitions and
prevents simultaneous strategy execution.
"""

from typing import Any, Optional, Union

from app.core.strategy.breakout import BreakoutStrategy
from app.domain.regime_classifier import RegimeClassifier
from app.domain.signal import CloseSignal, Signal
from app.domain.strategies.range_strategy import RangeStrategy


class StrategyEngine:
    """Single entry point for strategy evaluation with regime switching.

    * Only ONE strategy is active at a time.
    * Regime change while a position is open → CloseSignal.
    * No entry on the same candle as a regime-switch close.
    """

    def __init__(self, symbol: str = "BTCUSDT") -> None:
        self.regime_classifier = RegimeClassifier()
        self.breakout_strategy = BreakoutStrategy(symbol)
        self.range_strategy = RangeStrategy(symbol)
        self._active_regime: Optional[str] = None
        self._last_regime_changed: bool = False

    # ── Public API ─────────────────────────────────────────────────

    def evaluate(
        self,
        candles: list[dict],
        current_position: Any,
    ) -> Optional[Union[Signal, CloseSignal]]:
        """Evaluate candles under the current regime.

        Returns:
            Signal       – new entry (LONG or SHORT)
            CloseSignal  – close existing position (regime switch)
            None         – no action
        """
        regime = self.regime_classifier.evaluate(candles)
        regime_changed = (
            self._active_regime is not None and regime != self._active_regime
        )
        self._last_regime_changed = regime_changed

        # Regime switch with open position → close first
        if regime_changed and current_position is not None:
            self._active_regime = regime
            self._evaluate_active_strategy(candles)  # populate insight
            return CloseSignal(reason=f"Regime Switch → {regime}")

        self._active_regime = regime
        return self._evaluate_active_strategy(candles)

    def evaluate_for_insight(self, candles: list[dict]) -> None:
        """Insight seeding only — no position awareness, no CloseSignal."""
        regime = self.regime_classifier.evaluate(candles)
        self._active_regime = regime
        self._evaluate_active_strategy(candles)

    def get_last_evaluation_details(self) -> dict:
        """Merged insight: active strategy details + regime classifier."""
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
        details["regime_classifier"] = self.regime_classifier.get_last_details()
        return details

    # ── Internal ───────────────────────────────────────────────────

    def _evaluate_active_strategy(
        self, candles: list[dict]
    ) -> Optional[Signal]:
        if self._active_regime == "TRENDING":
            return self.breakout_strategy.evaluate(candles)
        if self._active_regime == "RANGING":
            return self.range_strategy.evaluate(candles)
        return None
