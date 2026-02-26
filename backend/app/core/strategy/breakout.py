"""15m BB compression + breakout + volume + overextension strategy."""

from typing import Optional

from app.domain.enums import Side
from app.domain.signal import Signal


def _sma(values: list[float], period: int) -> list[float]:
    """Simple moving average."""
    result: list[float] = []
    for i in range(len(values)):
        if i < period - 1:
            result.append(0.0)
        else:
            result.append(sum(values[i - period + 1 : i + 1]) / period)
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


def _bollinger_bands(
    closes: list[float], period: int = 20, num_std: float = 2.0
) -> tuple[list[float], list[float], list[float]]:
    """Returns (middle, upper, lower) bands."""
    sma_vals = _sma(closes, period)
    std_list: list[float] = []
    for i in range(len(closes)):
        if i < period - 1:
            std_list.append(0.0)
        else:
            slice_vals = closes[i - period + 1 : i + 1]
            mean = sum(slice_vals) / period
            variance = sum((x - mean) ** 2 for x in slice_vals) / period
            std = variance ** 0.5 if variance > 0 else 0.0
            std_list.append(std)
    upper = [sma_vals[i] + num_std * std_list[i] for i in range(len(closes))]
    lower = [sma_vals[i] - num_std * std_list[i] for i in range(len(closes))]
    return sma_vals, upper, lower


def _bb_width_percentile(band_widths: list[float], lookback: int) -> Optional[float]:
    """Percentile rank (0-100) of current width in lookback window."""
    if lookback <= 1:
        return None
    if len(band_widths) < lookback:
        return None
    window = band_widths[-lookback:]
    if len(window) != lookback or len(window) <= 1:
        return None
    current = band_widths[-1]
    sorted_window = sorted(window)
    rank = sum(1 for w in sorted_window if w < current)
    return (rank / (len(window) - 1)) * 100.0


def _average_volume(candles: list[dict], period: int = 20) -> Optional[float]:
    """20-period average volume."""
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
    """15m BB compression + breakout + volume + overextension filter."""

    BB_PERIOD = 20
    BB_NUM_STD = 2.0
    LOOKBACK = 100
    BREAKOUT_LOOKBACK = 20
    BREAKOUT_THRESHOLD = 0.001
    VOLUME_LOOKBACK = 20
    VOLUME_MULTIPLIER = 1.5
    OVEREXTENSION_LOOKBACK = 10
    ATR_PERIOD = 14
    ATR_OVEREXTENSION = 2.5

    def __init__(self, symbol: str = "BTCUSDT") -> None:
        self._symbol = symbol
        self._last_evaluation: dict = {}

    def get_last_evaluation_details(self) -> dict:
        """Return last evaluation context for insight API."""
        return dict(self._last_evaluation)

    def _store_evaluation(
        self,
        bb_width_pct: Optional[float],
        atr_value: float,
        volume_ratio: float,
        regime: str,
        engine_reasoning: list[str],
        close_price: float = 0.0,
    ) -> None:
        """Store last evaluation for insight API."""
        vol_score = (atr_value / close_price) * 100.0 if close_price > 0 else 0.0
        self._last_evaluation = {
            "regime": regime,
            "volatility_score": round(vol_score, 4),
            "bb_width_percentile": round(bb_width_pct, 2) if bb_width_pct is not None else 0.0,
            "atr_value": round(atr_value, 4),
            "volume_ratio": round(volume_ratio, 4),
            "engine_reasoning": engine_reasoning,
        }

    def evaluate(self, candles: list[dict]) -> Optional[Signal]:
        """
        Evaluate closed candles. Returns Signal(LONG/SHORT) or None.
        """
        required_keys = ("open", "high", "low", "close", "volume")
        if not candles or not all(
            isinstance(c, dict) and all(k in c for k in required_keys)
            for c in candles
        ):
            return None
        minimum_required = max(
            self.LOOKBACK,
            self.BB_PERIOD,
            self.ATR_PERIOD + 1,
            self.BREAKOUT_LOOKBACK + 1,
            self.VOLUME_LOOKBACK,
        )
        if len(candles) < minimum_required:
            return None
        closes = [c["close"] for c in candles]
        timestamps = [c.get("timestamp", "") for c in candles]

        middle, upper, lower = _bollinger_bands(closes, self.BB_PERIOD, self.BB_NUM_STD)
        band_widths = [
            (u - l) / m if m and m > 0 else 0.0
            for u, l, m in zip(upper, lower, middle)
        ]
        # Compression: use width of candle before signal (pre-breakout state)
        comp_widths = band_widths[:-1] if len(band_widths) >= 2 else band_widths
        if len(comp_widths) < self.LOOKBACK:
            return None
        bb_width_pct = _bb_width_percentile(comp_widths, self.LOOKBACK)
        close_price = candles[-1]["close"]
        if bb_width_pct is None or bb_width_pct > 20:
            self._store_evaluation(None, 0.0, 0.0, "Ranging", [
                "BB width percentile > 20 or N/A → no compression"
            ], close_price)
            return None

        atr_vals = _atr(candles, self.ATR_PERIOD)
        if len(atr_vals) != len(candles):
            return None
        current_atr = atr_vals[-1]
        if current_atr <= 0:
            self._store_evaluation(bb_width_pct, 0.0, 0.0, "Ranging", ["ATR invalid"], close_price)
            return None
        movement = _last_n_candles_movement(candles, self.OVEREXTENSION_LOOKBACK)
        overext_threshold = (
            self.ATR_OVEREXTENSION * current_atr * self.OVEREXTENSION_LOOKBACK
        )
        current_vol = candles[-1]["volume"]
        avg_vol = _average_volume(candles, self.VOLUME_LOOKBACK)
        volume_ratio = current_vol / avg_vol if avg_vol and avg_vol > 0 else 0.0

        if movement >= overext_threshold:
            self._store_evaluation(bb_width_pct, current_atr, volume_ratio, "High Volatility", [
                f"BB width below {int(bb_width_pct)}th percentile → compression",
                f"Movement {movement:.2f} >= overextension threshold {overext_threshold:.2f} → filtered",
            ], close_price)
            return None

        if avg_vol is None or avg_vol <= 0:
            self._store_evaluation(bb_width_pct, current_atr, volume_ratio, "Ranging", [
                f"BB width below {int(bb_width_pct)}th percentile",
                "Average volume not available",
            ], close_price)
            return None
        if current_vol < self.VOLUME_MULTIPLIER * avg_vol:
            self._store_evaluation(bb_width_pct, current_atr, volume_ratio, "Ranging", [
                f"BB width below {int(bb_width_pct)}th percentile → compression",
                f"Volume {volume_ratio:.2f}x below threshold {self.VOLUME_MULTIPLIER}x",
            ], close_price)
            return None

        prev_20 = candles[-(self.BREAKOUT_LOOKBACK + 1) : -1]
        high_20 = max(c["high"] for c in prev_20)
        low_20 = min(c["low"] for c in prev_20)
        close = candles[-1]["close"]
        signal_time = timestamps[-1] if timestamps else ""
        signal_candle_ts = signal_time

        if close >= high_20 * (1 + self.BREAKOUT_THRESHOLD):
            self._store_evaluation(bb_width_pct, current_atr, volume_ratio, "Trending", [
                f"BB width below {int(bb_width_pct)}th percentile → compression",
                f"Volume {volume_ratio:.2f}x above average",
                "Breakout confirmed above 20-bar high",
            ], close_price)
            return Signal(
                symbol=self._symbol,
                side=Side.LONG,
                signal_time=signal_time,
                signal_candle_ts=signal_candle_ts,
            )
        if close <= low_20 * (1 - self.BREAKOUT_THRESHOLD):
            self._store_evaluation(bb_width_pct, current_atr, volume_ratio, "Trending", [
                f"BB width below {int(bb_width_pct)}th percentile → compression",
                f"Volume {volume_ratio:.2f}x above average",
                "Breakout confirmed below 20-bar low",
            ], close_price)
            return Signal(
                symbol=self._symbol,
                side=Side.SHORT,
                signal_time=signal_time,
                signal_candle_ts=signal_candle_ts,
            )
        self._store_evaluation(bb_width_pct, current_atr, volume_ratio, "Ranging", [
            f"BB width below {int(bb_width_pct)}th percentile",
            f"Volume {volume_ratio:.2f}x above average",
            "No breakout above high or below low",
        ], close_price)
        return None
