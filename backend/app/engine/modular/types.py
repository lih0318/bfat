"""
Shared dataclasses for modular engine.
All inter-module data passed as these structured types.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np


@dataclass
class MarketSnapshot:
    """Market and account data fetched by execution_engine from Binance."""
    symbols: list[str]
    closes_map: dict[str, np.ndarray]
    vol_map: dict[str, float]
    atr_map: dict[str, float]
    funding_map: dict[str, float]
    price_map: dict[str, float]
    positions: list[dict[str, Any]]
    equity: float
    available_balance: float
    penalty_map: dict[str, float] = field(default_factory=dict)
    filters_map: dict[str, dict[str, Any]] = field(default_factory=dict)
    hlc_map: dict[str, dict[str, np.ndarray]] = field(default_factory=dict)
    # Multi-TF for BTC trend / ALT mean-reversion
    closes_4h_map: dict[str, np.ndarray] = field(default_factory=dict)
    closes_15m_map: dict[str, np.ndarray] = field(default_factory=dict)
    hlc_15m_map: dict[str, dict[str, np.ndarray]] = field(default_factory=dict)
    closes_5m_map: dict[str, np.ndarray] = field(default_factory=dict)
    volume_5m_map: dict[str, np.ndarray] = field(default_factory=dict)


@dataclass
class SignalSnapshot:
    """Per-symbol signal output (internal to signal_engine)."""
    symbol: str
    trend_score_raw: float = 0.0
    trend_score: float = 0.0
    rsi: float = 50.0
    rsi_scale: float = 1.0
    funding_rate: float = 0.0
    funding_scale: float = 1.0
    final_score: float = 0.0


@dataclass
class SignalOutput:
    """Per-symbol signal_engine output: direction, confidence, market_regime."""
    symbol: str
    direction: str  # "LONG" | "SHORT" | "NONE"
    confidence: float  # 0.0 ~ 1.0
    market_regime: str  # e.g. "trending_up", "ranging", "trending_down"
    final_score: float = 0.0


@dataclass
class SignalResult:
    """signal_engine output: symbol → SignalOutput."""
    outputs: dict[str, SignalOutput] = field(default_factory=dict)
    snapshots: dict[str, SignalSnapshot] = field(default_factory=dict)


@dataclass
class RiskDecision:
    """risk_engine output per symbol."""
    symbol: str
    allowed: bool
    position_size: float
    leverage: int
    exposure_pct: float
    reason: str = ""
    side: str = ""
    stop_price: float = 0.0  # SL price (0 = not set)


@dataclass
class RiskResult:
    """risk_engine output."""
    decisions: dict[str, RiskDecision] = field(default_factory=dict)
    total_exposure_pct: float = 0.0


@dataclass
class TradeRecord:
    """Single closed trade for optimizer input."""
    pnl: float = 0.0
    win: bool = False
    mae: float = 0.0
    mfe: float = 0.0
    holding_time_sec: float = 0.0
    symbol: str = ""
    ts: float = 0.0


@dataclass
class PerformanceLog:
    """Performance data for optimizer_engine."""
    equity: float = 0.0
    pnl_pct: float = 0.0
    win_rate: float = 0.0
    drawdown_pct: float = 0.0
    trade_count: int = 0
    ts: float = 0.0


@dataclass
class ConfigUpdate:
    """optimizer_engine output: parameter overrides."""
    overrides: dict[str, Any] = field(default_factory=dict)


@dataclass
class TargetPosition:
    """Single symbol target from sizing."""
    symbol: str
    side: str
    weight: float
    target_qty: float
    target_notional: float
    computed_leverage: int
    trend_score: float = 0.0
    stop_price: float = 0.0  # from risk_engine when set


@dataclass
class OrderPlan:
    """sizing_engine output: target positions to execute."""
    targets: dict[str, TargetPosition] = field(default_factory=dict)
    gross_notional: float = 0.0
    equity: float = 0.0
    drop_reason: str = ""


@dataclass
class ExecutionResult:
    """execution_engine output: execution report."""
    success: bool = False
    fills: list[dict[str, Any]] = field(default_factory=list)
    order_ids: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    activity: list[dict[str, Any]] = field(default_factory=list)
    report: dict[str, Any] = field(default_factory=dict)  # structured summary
