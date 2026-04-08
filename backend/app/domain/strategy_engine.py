"""Regime-switching strategy engine.

Delegates to BreakoutStrategy (TRENDING) or RangeStrategy (RANGING)
based on RegimeClassifier output.

Features:
  * Close-first policy: regime switch always closes existing position first
  * Strong-momentum conditional immediate entry after regime switch (FLAT only)
  * Score-based position sizing (0.7x / 1.0x / 1.2x)
  * Configurable cooldown per regime (TRENDING 1 bar / RANGING 0 bars)
  * `evaluate_ranging_intrabar` for live-bar range entry without waiting for close
"""

import logging
import time
from typing import Any, Optional, Union

from app.core.strategy.breakout import BreakoutStrategy

logger = logging.getLogger(__name__)
from app.domain.enums import Side
from app.domain.regime_classifier import RegimeClassifier, _adx
from app.domain.signal import CloseSignal, Signal
from app.domain.strategies.range_strategy import RangeStrategy

_MOMENTUM_ADX_THRESHOLD = 25
_MOMENTUM_VOL_Z_THRESHOLD = 1.0
_MOMENTUM_BODY_RATIO_THRESHOLD = 0.6
_MOMENTUM_MIN_CONDITIONS = 2

_COOLDOWN_BARS_TRENDING = 1  # reduced from 2 — one bar pause after weak-momentum switch to TRENDING
_COOLDOWN_BARS_RANGING = 0   # range intrabar entry allowed immediately after switch

# Strategy-driven exit thresholds (regime_classifier metrics)
_ADX_WEAK_EXIT = 18.0
_BB_CONTRACT_EXIT = 40.0
_STRUCTURE_BREAK_EXIT = 0.35


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
    * Close-first: regime change always closes existing position (regardless of PnL).
    * With an open position: optional CloseSignal from ADX/BB/structure exit rules (no new entries).
    * Strong-momentum required only when switching TO TRENDING; RANGING allows immediate eval.
    * Score-based position sizing applied to every entry signal.
    * `evaluate_ranging_intrabar` allows range entries inside a forming bar.
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
        self._last_skip_reason: Optional[str] = None
        self._last_trend_direction: str = "neutral"
        self._last_insight_update_ts: float = 0.0

    # ── Public API ─────────────────────────────────────────────────

    def evaluate(
        self,
        candles: list[dict],
        current_position: Any,
    ) -> Optional[Union[Signal, CloseSignal]]:
        """Evaluate candles under the current regime.

        Returns:
            Signal       – new entry (LONG or SHORT, with position_scale); only when flat
            CloseSignal  – close position (regime switch losing, or strategy exit)
            None         – no action (cooldown, hold open, no entry signal)
        """
        self._last_skip_reason = None
        regime = self.regime_classifier.evaluate(candles)
        rc_details = self.regime_classifier.get_last_details()
        score = rc_details.get("score", 0)
        self._last_trend_direction = rc_details.get("trend_direction", "neutral")

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
                return CloseSignal(reason="Regime Switch - Close First")

            # ── No position → apply entry cooldown ──
            if regime == "TRENDING":
                if not self._check_strong_momentum(candles):
                    self._regime_switch_cooldown = _COOLDOWN_BARS_TRENDING
                    self._last_skip_reason = "regime_switch_weak_momentum"
                    return None
            elif regime == "RANGING":
                self._regime_switch_cooldown = _COOLDOWN_BARS_RANGING
                if _COOLDOWN_BARS_RANGING > 0:
                    self._last_skip_reason = "regime_switch_ranging_cooldown"
                    return None
        else:
            self._active_regime = regime

        # ── 2. Open position: strategy-driven exit (no new entries) ─
        if current_position is not None:
            if self._active_regime == "RANGING":
                self.range_strategy.refresh_range_context(candles)
            exit_sig = self._evaluate_strategy_exit(candles, current_position)
            if exit_sig is not None:
                return exit_sig
            self._last_skip_reason = "hold_open_position"
            return None

        # ── 3. Entry cooldown gate (FLAT only) ────────────────────
        if self._regime_switch_cooldown > 0:
            self._regime_switch_cooldown -= 1
            self._last_skip_reason = f"cooldown ({self._regime_switch_cooldown + 1} bars remaining)"
            return None

        # ── 4. Active strategy evaluation ─────────────────────────
        if self._active_regime == "RANGING":
            self.range_strategy.refresh_range_context(candles)
            self._last_skip_reason = "ranging_deferred_to_intrabar"
            return None
        signal = self._evaluate_active_strategy(candles)

        if signal is None:
            self._last_skip_reason = "no_signal_from_strategy"

        # ── 5. Apply position scale ───────────────────────────────
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
        """Insight seeding — evaluate BOTH strategies so both sights are available.

        Open positions skip active-strategy ``evaluate()`` (no new entries), so Insight
        depends on this path. Range metrics (High/Mid/Low) must stay fresh: run range
        first and isolate failures so a breakout error cannot block range updates.
        """
        regime = self.regime_classifier.evaluate(candles)
        rc_details = self.regime_classifier.get_last_details()
        self._active_regime = regime
        self._last_score = rc_details.get("score", 0)
        self._last_position_scale = _position_scale_for_score(self._last_score)
        self._last_trend_direction = rc_details.get("trend_direction", "neutral")
        try:
            self.range_strategy.evaluate(candles)
        except Exception:
            logger.exception("evaluate_for_insight: range_strategy.evaluate failed")
        try:
            self.breakout_strategy.evaluate(candles, self._last_trend_direction)
        except Exception:
            logger.exception("evaluate_for_insight: breakout_strategy.evaluate failed")
        self._last_insight_update_ts = time.time()

    def evaluate_for_insight_live(self, candles: list[dict], live_bar: dict) -> None:
        """Update Insight using the forming bar without generating trade signals.

        Called on every forming-candle tick so Insight stays fresh even when a
        position is open and ``evaluate()`` skips entry logic.
        """
        regime = self.regime_classifier.evaluate(candles)
        rc_details = self.regime_classifier.get_last_details()
        self._active_regime = regime
        self._last_score = rc_details.get("score", 0)
        self._last_position_scale = _position_scale_for_score(self._last_score)
        self._last_trend_direction = rc_details.get("trend_direction", "neutral")
        try:
            self.range_strategy.update_insight_live(candles, live_bar)
        except Exception:
            logger.exception("evaluate_for_insight_live: range_strategy failed")
        try:
            self.breakout_strategy.update_insight_live(candles, live_bar, self._last_trend_direction)
        except Exception:
            logger.exception("evaluate_for_insight_live: breakout_strategy failed")
        self._last_insight_update_ts = time.time()

    def evaluate_ranging_intrabar(
        self,
        candles: list[dict],
        live_bar: dict,
        current_position: Any,
    ) -> Optional[Signal]:
        """Evaluate Range intrabar entry using the forming bar.

        Only fires when:
          * Last confirmed regime is RANGING
          * No open position (FLAT)
          * No cooldown remaining
          * Kill switch not already checked here (engine handles that)

        Returns Signal with position_scale applied, or None.
        """
        if self._active_regime != "RANGING":
            return None
        if current_position is not None:
            return None
        if self._regime_switch_cooldown > 0:
            return None

        signal = self.range_strategy.evaluate_intrabar(candles, live_bar)
        if signal is None:
            return None

        position_scale = self._last_position_scale
        return Signal(
            symbol=signal.symbol,
            side=signal.side,
            signal_time=signal.signal_time,
            signal_candle_ts=signal.signal_candle_ts,
            stop_price=signal.stop_price,
            take_profit=signal.take_profit,
            position_scale=position_scale,
        )

    def get_last_evaluation_details(self) -> dict:
        """Merged insight: active strategy + trend_reference + range_reference."""
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
        details["skip_reason"] = self._last_skip_reason
        details["regime_classifier"] = self.regime_classifier.get_last_details()
        details["last_insight_update_ts"] = self._last_insight_update_ts

        bd = self.breakout_strategy.get_last_evaluation_details()
        details["trend_reference"] = {
            "volatility_score": bd.get("volatility_score"),
            "atr_value": bd.get("atr_value"),
            "volume_ratio": bd.get("volume_ratio"),
            "ema_fast": bd.get("ema_fast"),
            "ema_slow": bd.get("ema_slow"),
        }
        rd = self.range_strategy.get_last_evaluation_details()
        details["range_reference"] = {
            "range_high": rd.get("range_high"),
            "range_low": rd.get("range_low"),
            "range_mid": rd.get("range_mid"),
            "rsi": rd.get("rsi"),
            "volume_zscore": rd.get("volume_zscore"),
            "close_price": rd.get("close_price"),
        }
        return details

    # ── Internal helpers ───────────────────────────────────────────

    def _evaluate_active_strategy(
        self, candles: list[dict]
    ) -> Optional[Signal]:
        if self._active_regime == "TRENDING":
            return self.breakout_strategy.evaluate(candles, self._last_trend_direction)
        if self._active_regime == "RANGING":
            return self.range_strategy.evaluate(candles)
        return None

    def _evaluate_strategy_exit(
        self, candles: list[dict], position: Any
    ) -> Optional[CloseSignal]:
        """Exit signals from regime classifier context (ADX / BB width / structure)."""
        _ = candles, position
        d = self.regime_classifier.get_last_details()
        adx = d.get("adx")
        if adx is not None and float(adx) < _ADX_WEAK_EXIT:
            return CloseSignal(reason="ADX Weakening")
        bb_pct = d.get("bb_width_percentile")
        if bb_pct is not None and float(bb_pct) < _BB_CONTRACT_EXIT:
            return CloseSignal(reason="BB Width Contraction")
        hh = d.get("hh_ratio")
        ll = d.get("ll_ratio")
        if hh is not None and ll is not None:
            if max(float(hh), float(ll)) < _STRUCTURE_BREAK_EXIT:
                return CloseSignal(reason="Structure Breakdown")
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
