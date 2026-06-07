"""
Modular engine configuration. Loads from modular_engine.json.
No dependency on app.engine.config_model.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.core.config import settings

logger = logging.getLogger(__name__)


class ModularConfig(BaseModel):
    """Configuration for the modular engine pipeline."""

    signal_tf: Literal["1d", "4h"] = "1d"
    horizons: list[int] = [30, 90, 365]
    deadzone_threshold: float = Field(0.10, ge=0.0, le=1.0)
    vol_window: int = Field(60, ge=10, le=365)
    target_portfolio_vol: float = Field(0.18, gt=0.0, le=1.0)
    effective_leverage_target: float = Field(5.0, gt=0.0, le=20.0)
    stop_atr_window: int = Field(14, ge=5, le=100)
    stop_k: float = Field(2.0, gt=0.0, le=10.0)
    chandelier_atr_mult: float = Field(3.0, gt=0.0, le=10.0)
    tp1_r_multiple: float = Field(1.0, gt=0.0, le=10.0)
    tp2_r_multiple: float = Field(2.0, gt=0.0, le=20.0)
    tp1_close_pct: float = Field(0.50, gt=0.0, le=1.0)
    tp2_close_pct: float = Field(1.0, gt=0.0, le=1.0)
    breakeven_after_tp1: bool = True
    breakeven_offset_bps: int = Field(10, ge=0, le=100)
    execution_tick_sec: int = Field(120, ge=60, le=300)
    execution_threshold_pct: float = Field(0.02, ge=0.0, le=0.5)
    entry_order_mode: Literal["POST_ONLY_LIMIT", "IOC_LIMIT", "MARKET"] = "IOC_LIMIT"
    ioc_epsilon: float = Field(0.0005, ge=0.0, le=0.01)
    top_k_enabled: bool = True
    top_k: int = Field(5, ge=1, le=50)
    replace_threshold: float = Field(0.20, ge=0.0, le=1.0)
    min_weight_floor: float = Field(0.02, ge=0.0, le=0.5)
    max_weight_cap: float = Field(0.40, ge=0.05, le=1.0)
    rsi_period: int = Field(14, ge=5, le=50)
    rsi_overbought: float = Field(70.0, ge=50, le=100)
    rsi_oversold: float = Field(30.0, ge=0, le=50)
    rsi_scale_overbought: float = Field(0.5, ge=0.0, le=1.0)
    rsi_scale_oversold: float = Field(1.5, ge=1.0, le=3.0)
    funding_scale_enabled: bool = False
    universe_mode: Literal["all", "alt_only"] = "all"
    universe_top_n: int = Field(20, ge=1, le=200)
    listing_age_days: int = Field(90, ge=0, le=3650)
    max_spread_pct: float = Field(0.15, ge=0.0, le=5.0)
    margin_mode: Literal["ISOLATED", "CROSSED"] = "ISOLATED"
    risk_per_trade_pct: float = Field(0.005, ge=0.003, le=0.008)
    atr_stop_mult: float = Field(1.5, ge=1.2, le=2.2)
    btc_pullback_tolerance_pct: float = Field(0.005, ge=0.002, le=0.015)
    alt_rsi_long_max: float = Field(30.0, ge=20.0, le=40.0)
    alt_rsi_short_min: float = Field(70.0, ge=60.0, le=80.0)
    max_symbol_leverage: int = Field(20, ge=1, le=125)
    min_symbol_leverage: int = Field(1, ge=1, le=125)
    max_concurrent_symbols: int = Field(10, ge=1, le=50)
    reserve_margin_buffer_pct: float = Field(0.10, ge=0.0, le=0.50)
    drawdown_kill_pct: float = Field(0.15, ge=0.01, le=1.0)
    symbol: str = "BTCUSDT"
    adaptive_leverage_enabled: bool = True
    min_concentration_pct: float = Field(0.50, ge=0.2, le=0.95)
    max_concentration_pct: float = Field(0.95, ge=0.5, le=1.0)


def _modular_config_path() -> Path:
    p = settings.config_dir / "modular_engine.json"
    settings.config_dir.mkdir(parents=True, exist_ok=True)
    return p


def load_modular_config() -> ModularConfig:
    """Load from modular_engine.json or return defaults."""
    path = _modular_config_path()
    if path.exists():
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            return ModularConfig.model_validate(data)
        except Exception as exc:
            logger.warning("Failed to load modular config: %s", exc)
    return ModularConfig()


def save_modular_config(cfg: ModularConfig) -> None:
    path = _modular_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cfg.model_dump(), f, indent=2, ensure_ascii=False)
