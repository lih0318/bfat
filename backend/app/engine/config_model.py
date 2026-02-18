"""
Engine configuration model (Pydantic v2).
Replaces old AutopilotConfig with TSMOM-aligned parameters.
Saved to config_dir/engine.json.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

from app.core.config import settings

logger = logging.getLogger(__name__)


class EngineConfig(BaseModel):
    """Full configuration for the TSMOM trend engine."""

    # ── Profile ──────────────────────────────────────────────────
    profile: Literal["conservative", "balanced", "aggressive", "custom"] = Field(
        "balanced", description="Preset profile or custom"
    )

    # ── Signal ───────────────────────────────────────────────────
    signal_tf: Literal["1d", "4h"] = Field("1d", description="Signal timeframe")
    horizons: list[int] = Field(
        default=[30, 90, 365],
        description="Calendar-day momentum look-back windows",
    )
    deadzone_threshold: float = Field(
        0.10, ge=0.0, le=1.0,
        description="TrendScore magnitude below this → flat (no position)",
    )

    # ── Volatility / Sizing ──────────────────────────────────────
    vol_window: int = Field(60, ge=10, le=365, description="Days for realized-vol estimate")
    target_portfolio_vol: float = Field(
        0.18, gt=0.0, le=1.0,
        description="Annualized target portfolio volatility",
    )
    effective_leverage_target: float = Field(
        5.0, gt=0.0, le=20.0,
        description="Soft guide for gross leverage (portfolio notional / equity)",
    )

    # ── Stop / Bracket ───────────────────────────────────────────
    stop_atr_window: int = Field(14, ge=5, le=100, description="ATR window for stop distance")
    stop_k: float = Field(2.0, gt=0.0, le=10.0, description="Stop = entry ± k × ATR")
    trailing_stop: bool = Field(False, description="Enable trailing stop")

    # ── Chandelier / Partial TP ──────────────────────────────────
    chandelier_atr_mult: float = Field(
        3.0, gt=0.0, le=10.0,
        description="Chandelier SL = entry ± mult × ATR (replaces fixed stop_k for brackets)",
    )
    tp1_r_multiple: float = Field(
        1.0, gt=0.0, le=10.0,
        description="TP1 distance as multiple of SL distance (1R)",
    )
    tp2_r_multiple: float = Field(
        2.0, gt=0.0, le=20.0,
        description="TP2 distance as multiple of SL distance (2R)",
    )
    tp1_close_pct: float = Field(
        0.50, gt=0.0, le=1.0,
        description="Fraction of position to close at TP1 (e.g. 0.50 = 50%)",
    )
    tp2_close_pct: float = Field(
        1.0, gt=0.0, le=1.0,
        description="Fraction of remaining position to close at TP2 (1.0 = all remaining)",
    )
    breakeven_after_tp1: bool = Field(
        True,
        description="Move SL to breakeven (+offset) after TP1 is filled",
    )
    breakeven_offset_bps: int = Field(
        10, ge=0, le=100,
        description="Breakeven offset in basis points above/below entry (e.g. 10 = 0.1%)",
    )

    # ── Execution ────────────────────────────────────────────────
    execution_tick_sec: int = Field(
        120, ge=60, le=300,
        description="Seconds between execution ticks",
    )
    execution_threshold_pct: float = Field(
        0.02, ge=0.0, le=0.5,
        description="Min delta as fraction of equity to trigger order",
    )
    entry_order_mode: Literal["POST_ONLY_LIMIT", "IOC_LIMIT", "MARKET"] = Field(
        "IOC_LIMIT", description="Entry order type"
    )
    ioc_epsilon: float = Field(
        0.0005, ge=0.0, le=0.01,
        description="IOC limit price offset from mid (fraction)",
    )

    # ── Top-K ────────────────────────────────────────────────────
    top_k_enabled: bool = Field(True, description="Enable Top-K concentration")
    top_k: int = Field(5, ge=1, le=50, description="Max number of positions")
    replace_threshold: float = Field(
        0.20, ge=0.0, le=1.0,
        description="New symbol must beat incumbents TrendScore by this margin",
    )
    min_weight_floor: float = Field(
        0.02, ge=0.0, le=0.5,
        description="Minimum weight per symbol (below → zero)",
    )
    max_weight_cap: float = Field(
        0.40, ge=0.05, le=1.0,
        description="Maximum weight per symbol",
    )

    # ── RSI overlay ──────────────────────────────────────────────
    rsi_period: int = Field(14, ge=5, le=50)
    rsi_overbought: float = Field(70.0, ge=50, le=100)
    rsi_oversold: float = Field(30.0, ge=0, le=50)
    rsi_scale_overbought: float = Field(
        0.5, ge=0.0, le=1.0,
        description="Scale factor when RSI > overbought (shrink long)",
    )
    rsi_scale_oversold: float = Field(
        1.5, ge=1.0, le=3.0,
        description="Scale factor when RSI < oversold (boost long)",
    )

    # ── Funding overlay ──────────────────────────────────────────
    funding_scale_enabled: bool = Field(False, description="Enable funding-rate overlay")

    # ── Universe ─────────────────────────────────────────────────
    universe_mode: Literal["all", "alt_only"] = Field(
        "all",
        description="all=기존 전체, alt_only=BTC/ETH 제외 알트만",
    )
    universe_top_n: int = Field(20, ge=1, le=200, description="Top-N by 24h volume")
    listing_age_days: int = Field(90, ge=0, le=3650, description="Min listing age in days")
    max_spread_pct: float = Field(
        0.15, ge=0.0, le=5.0,
        description="Max bid-ask spread % to include symbol",
    )

    # ── Margin / Risk ────────────────────────────────────────────
    margin_mode: Literal["ISOLATED", "CROSSED"] = Field(
        "ISOLATED",
        description="Per-symbol margin mode. ISOLATED recommended for multi-symbol safety",
    )
    risk_per_trade_pct: float = Field(
        0.01, gt=0.0, le=0.10,
        description="Max loss per trade as fraction of equity (e.g. 0.01 = 1%)",
    )
    max_symbol_leverage: int = Field(
        20, ge=1, le=125,
        description="Per-symbol leverage upper bound (Binance setting)",
    )
    min_symbol_leverage: int = Field(
        1, ge=1, le=125,
        description="Per-symbol leverage lower bound",
    )
    max_concurrent_symbols: int = Field(
        10, ge=1, le=50,
        description="Max number of symbols with open positions simultaneously",
    )
    reserve_margin_buffer_pct: float = Field(
        0.10, ge=0.0, le=0.50,
        description="Fraction of equity to keep as reserve (not used for margin)",
    )
    drawdown_kill_pct: float = Field(
        0.15, ge=0.01, le=1.0,
        description="Kill switch: stop engine if drawdown exceeds this fraction",
    )

    # ── Alerts (optional) ────────────────────────────────────────
    alerts_telegram_bot_token: Optional[str] = Field(None)
    alerts_telegram_chat_id: Optional[str] = Field(None)

    # ── Misc ─────────────────────────────────────────────────────
    symbol: str = Field(
        "BTCUSDT",
        description="Default symbol (used for market-regime display & single-symbol mode)",
    )

    def model_dump_safe(self) -> dict[str, Any]:
        """Dump without secrets."""
        d = self.model_dump()
        if d.get("alerts_telegram_bot_token"):
            d["alerts_telegram_bot_token"] = "***"
        return d


# ── Persistence helpers ──────────────────────────────────────────


def _engine_config_path() -> Path:
    p = settings.config_dir / "engine.json"
    settings.config_dir.mkdir(parents=True, exist_ok=True)
    return p


def load_engine_config() -> EngineConfig:
    """Load from disk or return defaults."""
    path = _engine_config_path()
    if path.exists():
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            return EngineConfig.model_validate(data)
        except Exception as exc:
            logger.warning("Failed to load engine config: %s", exc)
    return EngineConfig()


def save_engine_config(cfg: EngineConfig) -> None:
    path = _engine_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cfg.model_dump_safe(), f, indent=2, ensure_ascii=False)
