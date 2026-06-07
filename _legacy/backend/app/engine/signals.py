"""
Signal generation: TSMOM TrendScore with deadzone, RSI overlay, funding overlay.

TrendScore(symbol) = mean(S_h) for h in horizons
  S_h = sign(close / close_{h days ago} − 1)
Deadzone: |TrendScore| < threshold → 0
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from app.engine.datafeed import compute_rsi

logger = logging.getLogger(__name__)

# Approximate bars per day for common timeframes
_BARS_PER_DAY: dict[str, float] = {
    "1d": 1.0,
    "4h": 6.0,
    "1h": 24.0,
}


@dataclass
class SignalSnapshot:
    """Per-symbol signal output."""
    symbol: str
    trend_score_raw: float = 0.0
    trend_score: float = 0.0  # after deadzone
    horizon_signals: dict[int, float] = field(default_factory=dict)
    rsi: float = 50.0
    rsi_scale: float = 1.0
    funding_rate: float = 0.0
    funding_scale: float = 1.0
    final_score: float = 0.0  # trend_score * rsi_scale * funding_scale


def _horizon_signal(closes: np.ndarray, horizon_bars: int) -> float:
    """S_h = sign(close_now / close_{h bars ago} − 1)."""
    if len(closes) <= horizon_bars:
        return 0.0
    ret = closes[-1] / closes[-1 - horizon_bars] - 1.0
    if ret > 0:
        return 1.0
    elif ret < 0:
        return -1.0
    return 0.0


def compute_trend_scores(
    closes_map: dict[str, np.ndarray],
    horizons: list[int],
    signal_tf: str,
    deadzone: float = 0.10,
) -> dict[str, SignalSnapshot]:
    """
    Compute TrendScore for each symbol.
    ``horizons`` are in calendar days; converted to bars via signal_tf.
    """
    bpd = _BARS_PER_DAY.get(signal_tf, 1.0)
    results: dict[str, SignalSnapshot] = {}

    for sym, closes in closes_map.items():
        snap = SignalSnapshot(symbol=sym)
        signals: list[float] = []
        for h_days in horizons:
            h_bars = max(1, int(round(h_days * bpd)))
            s = _horizon_signal(closes, h_bars)
            snap.horizon_signals[h_days] = s
            signals.append(s)

        if signals:
            snap.trend_score_raw = float(np.mean(signals))
        else:
            snap.trend_score_raw = 0.0

        # Deadzone
        if abs(snap.trend_score_raw) < deadzone:
            snap.trend_score = 0.0
        else:
            snap.trend_score = snap.trend_score_raw

        snap.final_score = snap.trend_score
        results[sym] = snap

    return results


def apply_rsi_overlay(
    snapshots: dict[str, SignalSnapshot],
    closes_map: dict[str, np.ndarray],
    rsi_period: int = 14,
    rsi_overbought: float = 70.0,
    rsi_oversold: float = 30.0,
    scale_overbought: float = 0.5,
    scale_oversold: float = 1.5,
) -> None:
    """Apply RSI-based scaling to trend scores (in-place)."""
    for sym, snap in snapshots.items():
        closes = closes_map.get(sym)
        if closes is None or len(closes) < rsi_period + 1:
            snap.rsi = 50.0
            snap.rsi_scale = 1.0
            continue
        rsi = compute_rsi(closes, rsi_period)
        snap.rsi = rsi

        if snap.trend_score > 0:
            # Long signal
            if rsi >= rsi_overbought:
                snap.rsi_scale = scale_overbought  # reduce long
            elif rsi <= rsi_oversold:
                snap.rsi_scale = scale_oversold  # boost long
            else:
                snap.rsi_scale = 1.0
        elif snap.trend_score < 0:
            # Short signal (mirror)
            if rsi <= rsi_oversold:
                snap.rsi_scale = scale_overbought  # reduce short
            elif rsi >= rsi_overbought:
                snap.rsi_scale = scale_oversold  # boost short
            else:
                snap.rsi_scale = 1.0
        else:
            snap.rsi_scale = 1.0

        snap.final_score = snap.trend_score * snap.rsi_scale


def apply_funding_overlay(
    snapshots: dict[str, SignalSnapshot],
    funding_map: dict[str, float],
) -> None:
    """
    Funding overlay: if funding opposes our direction, scale down.
    Positive funding = longs pay shorts → penalise longs.
    """
    for sym, snap in snapshots.items():
        fr = funding_map.get(sym, 0.0)
        snap.funding_rate = fr

        if snap.trend_score > 0 and fr > 0.0005:
            # Long but high positive funding → scale down
            snap.funding_scale = max(0.5, 1.0 - fr * 100)
        elif snap.trend_score < 0 and fr < -0.0005:
            # Short but high negative funding → scale down
            snap.funding_scale = max(0.5, 1.0 + fr * 100)
        else:
            snap.funding_scale = 1.0

        snap.final_score = snap.trend_score * snap.rsi_scale * snap.funding_scale


def generate_reasoning(snapshot: SignalSnapshot) -> str:
    """Generate Korean one-line reasoning statement for a signal snapshot."""
    parts = []
    
    # Horizon analysis
    horizons = snapshot.horizon_signals
    if horizons:
        up = sum(1 for v in horizons.values() if v > 0)
        down = sum(1 for v in horizons.values() if v < 0)
        total = len(horizons)
        
        if up > down:
            parts.append(f"{total}개 horizon 중 {up}개 상승 ↑")
        elif down > up:
            parts.append(f"{total}개 horizon 중 {down}개 하락 ↓")
        else:
            parts.append(f"{total}개 horizon 혼조")
    
    # RSI analysis
    rsi = snapshot.rsi
    if rsi >= 70:
        parts.append(f"RSI {rsi:.0f}(과매수)")
    elif rsi <= 30:
        parts.append(f"RSI {rsi:.0f}(과매도)")
    elif rsi >= 55:
        parts.append(f"RSI {rsi:.0f}(상승 편향)")
    elif rsi <= 45:
        parts.append(f"RSI {rsi:.0f}(하락 편향)")
    else:
        parts.append(f"RSI {rsi:.0f}(중립)")
    
    # Funding rate analysis
    fr = snapshot.funding_rate
    if abs(fr) < 0.0001:
        parts.append("펀딩비 안정")
    elif fr > 0.001:
        parts.append(f"펀딩비 높음({fr*100:.3f}%)")
    elif fr < -0.001:
        parts.append(f"펀딩비 낮음({fr*100:.3f}%)")
    else:
        parts.append("펀딩비 보통")
    
    # Overall direction
    if snapshot.final_score > 0.3:
        direction = "→ 강한 롱"
    elif snapshot.final_score > 0:
        direction = "→ 약한 롱"
    elif snapshot.final_score < -0.3:
        direction = "→ 강한 숏"
    elif snapshot.final_score < 0:
        direction = "→ 약한 숏"
    else:
        direction = "→ 시그널 없음"
    
    return ". ".join(parts) + ". " + direction
