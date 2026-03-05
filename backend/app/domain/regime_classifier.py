"""Regime classifier: TRENDING vs RANGING.

Score-based classification using ADX(14), BB width percentile, and structure ratio.
Hysteresis prevents whipsaw regime changes.
"""

from typing import Optional

# ─── Fixed thresholds (to avoid overfitting) ───────────────────────
ADX_THRESHOLD = 22
BB_WIDTH_PCT_THRESHOLD = 65
STRUCTURE_RATIO_THRESHOLD = 0.6
TRENDING_CONFIRM_BARS = 3
RANGING_CONFIRM_BARS = 5

# ─── Indicator parameters ──────────────────────────────────────────
ADX_PERIOD = 14
BB_PERIOD = 20
BB_NUM_STD = 2.0
BB_WIDTH_LOOKBACK = 100
STRUCTURE_LOOKBACK = 20
MINIMUM_CANDLES = 100  # max(100, 20, 14+1)


def _adx(candles: list[dict], period: int = ADX_PERIOD) -> Optional[float]:
    """Wilder-smoothed ADX. Returns last value or None if insufficient data.

    Requires >= 2*period + 1 candles.  All computations are past-only
    (current candle included only as the final observation, no lookahead).
    """
    n = len(candles)
    if n < 2 * period + 1:
        return None

    plus_dm: list[float] = []
    minus_dm: list[float] = []
    tr_list: list[float] = []

    for i in range(1, n):
        high = candles[i]["high"]
        low = candles[i]["low"]
        prev_high = candles[i - 1]["high"]
        prev_low = candles[i - 1]["low"]
        prev_close = candles[i - 1]["close"]

        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        tr_list.append(tr)

        up = high - prev_high
        down = prev_low - low
        plus_dm.append(up if up > down and up > 0 else 0.0)
        minus_dm.append(down if down > up and down > 0 else 0.0)

    # Wilder smoothing — first value = sum of first `period` observations
    tr_s = sum(tr_list[:period])
    plus_s = sum(plus_dm[:period])
    minus_s = sum(minus_dm[:period])

    dx_values: list[float] = []

    if tr_s > 0:
        pdi = (plus_s / tr_s) * 100
        mdi = (minus_s / tr_s) * 100
    else:
        pdi = mdi = 0.0
    di_sum = pdi + mdi
    dx_values.append(abs(pdi - mdi) / di_sum * 100 if di_sum > 0 else 0.0)

    for i in range(period, len(tr_list)):
        tr_s = tr_s - tr_s / period + tr_list[i]
        plus_s = plus_s - plus_s / period + plus_dm[i]
        minus_s = minus_s - minus_s / period + minus_dm[i]

        if tr_s > 0:
            pdi = (plus_s / tr_s) * 100
            mdi = (minus_s / tr_s) * 100
        else:
            pdi = mdi = 0.0
        di_sum = pdi + mdi
        dx_values.append(abs(pdi - mdi) / di_sum * 100 if di_sum > 0 else 0.0)

    if len(dx_values) < period:
        return None

    adx_val = sum(dx_values[:period]) / period
    for i in range(period, len(dx_values)):
        adx_val = (adx_val * (period - 1) + dx_values[i]) / period

    return adx_val


def _bb_width_percentile(
    candles: list[dict],
    bb_period: int = BB_PERIOD,
    bb_std: float = BB_NUM_STD,
    lookback: int = BB_WIDTH_LOOKBACK,
) -> Optional[float]:
    """BB width percentile (0-100).  Current width rank within lookback window.

    Replicates the same BB width calculation used in breakout.py.
    """
    closes = [c["close"] for c in candles]
    if len(closes) < max(bb_period, lookback):
        return None

    band_widths: list[float] = []
    for i in range(len(closes)):
        if i < bb_period - 1:
            band_widths.append(0.0)
            continue
        window = closes[i - bb_period + 1 : i + 1]
        mean = sum(window) / bb_period
        variance = sum((x - mean) ** 2 for x in window) / bb_period
        std = variance**0.5 if variance > 0 else 0.0
        upper = mean + bb_std * std
        lower = mean - bb_std * std
        width = (upper - lower) / mean if mean > 0 else 0.0
        band_widths.append(width)

    if len(band_widths) < lookback:
        return None

    pct_window = band_widths[-lookback:]
    if len(pct_window) <= 1:
        return None
    current = band_widths[-1]
    rank = sum(1 for w in pct_window if w < current)
    return (rank / (len(pct_window) - 1)) * 100.0


def _structure_ratio(
    candles: list[dict], lookback: int = STRUCTURE_LOOKBACK
) -> tuple[Optional[float], Optional[float]]:
    """Higher-High and Lower-Low ratios over last *lookback* bars.

    Returns (hh_ratio, ll_ratio) or (None, None) if insufficient data.
    ratio = count / (lookback - 1).
    """
    if len(candles) < lookback:
        return None, None

    recent = candles[-lookback:]
    hh_count = 0
    ll_count = 0
    pairs = lookback - 1

    for i in range(1, len(recent)):
        if recent[i]["high"] > recent[i - 1]["high"]:
            hh_count += 1
        if recent[i]["low"] < recent[i - 1]["low"]:
            ll_count += 1

    return hh_count / pairs, ll_count / pairs


class RegimeClassifier:
    """Score-based regime classifier with hysteresis.

    score = sum of:
        +1  if  ADX > 22
        +1  if  BB width percentile > 65
        +1  if  max(HH ratio, LL ratio) >= 0.6

    TRENDING confirmed after 3 consecutive bars with score >= 2.
    RANGING  confirmed after 5 consecutive bars with score < 2.
    """

    def __init__(self) -> None:
        self._current_regime: str = "RANGING"
        self._regime_counter: int = 0
        self._last_details: dict = {}

    @property
    def current_regime(self) -> str:
        return self._current_regime

    def get_last_details(self) -> dict:
        """Return last evaluation context for insight API."""
        return dict(self._last_details)

    def evaluate(self, candles: list[dict]) -> str:
        """Evaluate candles and return ``'TRENDING'`` or ``'RANGING'``.

        Side-effect: updates hysteresis counter and stores detail dict
        accessible via :meth:`get_last_details`.
        """
        if len(candles) < MINIMUM_CANDLES:
            self._last_details = {
                "regime": self._current_regime,
                "trend_direction": "neutral",
                "adx": None,
                "bb_width_percentile": None,
                "hh_ratio": None,
                "ll_ratio": None,
                "score": 0,
            }
            return self._current_regime

        adx = _adx(candles)
        bb_pct = _bb_width_percentile(candles)
        hh_ratio, ll_ratio = _structure_ratio(candles)

        score = 0
        if adx is not None and adx > ADX_THRESHOLD:
            score += 1
        if bb_pct is not None and bb_pct > BB_WIDTH_PCT_THRESHOLD:
            score += 1
        if (
            hh_ratio is not None
            and ll_ratio is not None
            and max(hh_ratio, ll_ratio) >= STRUCTURE_RATIO_THRESHOLD
        ):
            score += 1

        # ── Hysteresis ──
        if score >= 2:
            if self._current_regime == "TRENDING":
                self._regime_counter = 0
            else:
                self._regime_counter += 1
                if self._regime_counter >= TRENDING_CONFIRM_BARS:
                    self._current_regime = "TRENDING"
                    self._regime_counter = 0
        else:
            if self._current_regime == "RANGING":
                self._regime_counter = 0
            else:
                self._regime_counter += 1
                if self._regime_counter >= RANGING_CONFIRM_BARS:
                    self._current_regime = "RANGING"
                    self._regime_counter = 0

        trend_direction = "neutral"
        if hh_ratio is not None and ll_ratio is not None:
            if hh_ratio > ll_ratio:
                trend_direction = "up"
            elif ll_ratio > hh_ratio:
                trend_direction = "down"

        self._last_details = {
            "regime": self._current_regime,
            "trend_direction": trend_direction,
            "adx": round(adx, 4) if adx is not None else None,
            "bb_width_percentile": round(bb_pct, 2) if bb_pct is not None else None,
            "hh_ratio": round(hh_ratio, 4) if hh_ratio is not None else None,
            "ll_ratio": round(ll_ratio, 4) if ll_ratio is not None else None,
            "score": score,
        }
        return self._current_regime
