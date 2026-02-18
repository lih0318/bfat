"""
Three built-in profile presets (spec section 11).
Call ``apply_profile(cfg, "balanced")`` to overwrite relevant fields.
"""
from __future__ import annotations

from typing import Any

# ── Preset dictionaries ──────────────────────────────────────────

# ── Conservative ─────────────────────────────────────────────────
# 안정 우선. 레버리지 3배, 넓은 손절, 느린 실행, 분산 적음.
# 바이낸스 계좌 레버리지: 10배 (마진 효율), 실제 노출: 자본의 ~3배.
CONSERVATIVE: dict[str, Any] = {
    "signal_tf": "1d",
    "horizons": [30, 90, 365],
    "deadzone_threshold": 0.15,
    "vol_window": 60,
    "target_portfolio_vol": 0.12,
    "effective_leverage_target": 3.0,
    "stop_atr_window": 14,
    "stop_k": 2.5,
    "trailing_stop": False,
    "execution_tick_sec": 300,
    "execution_threshold_pct": 0.03,
    "entry_order_mode": "POST_ONLY_LIMIT",
    "ioc_epsilon": 0.0003,
    "top_k_enabled": True,
    "top_k": 3,
    "replace_threshold": 0.25,
    "min_weight_floor": 0.05,
    "max_weight_cap": 0.50,
    "rsi_period": 14,
    "rsi_overbought": 75.0,
    "rsi_oversold": 25.0,
    "rsi_scale_overbought": 0.3,
    "rsi_scale_oversold": 1.2,
    "funding_scale_enabled": False,
    "universe_mode": "all",
    "universe_top_n": 10,
    "listing_age_days": 180,
    "max_spread_pct": 0.10,
    "drawdown_kill_pct": 0.10,
    "margin_mode": "ISOLATED",
    "risk_per_trade_pct": 0.005,
    "max_symbol_leverage": 10,
    "min_symbol_leverage": 1,
    "max_concurrent_symbols": 5,
    "reserve_margin_buffer_pct": 0.15,
    "chandelier_atr_mult": 3.5,
    "tp1_r_multiple": 1.0,
    "tp2_r_multiple": 2.5,
    "tp1_close_pct": 0.50,
    "tp2_close_pct": 1.0,
    "breakeven_after_tp1": True,
    "breakeven_offset_bps": 15,
}

# ── Balanced ─────────────────────────────────────────────────────
# 수익-리스크 균형. 레버리지 5배, 표준 손절, 중간 속도.
# 바이낸스 계좌 레버리지: 10배, 실제 노출: 자본의 ~5배.
BALANCED: dict[str, Any] = {
    "signal_tf": "1d",
    "horizons": [30, 90, 365],
    "deadzone_threshold": 0.10,
    "vol_window": 60,
    "target_portfolio_vol": 0.18,
    "effective_leverage_target": 5.0,
    "stop_atr_window": 14,
    "stop_k": 2.0,
    "trailing_stop": False,
    "execution_tick_sec": 120,
    "execution_threshold_pct": 0.02,
    "entry_order_mode": "IOC_LIMIT",
    "ioc_epsilon": 0.0005,
    "top_k_enabled": True,
    "top_k": 5,
    "replace_threshold": 0.20,
    "min_weight_floor": 0.02,
    "max_weight_cap": 0.40,
    "rsi_period": 14,
    "rsi_overbought": 70.0,
    "rsi_oversold": 30.0,
    "rsi_scale_overbought": 0.5,
    "rsi_scale_oversold": 1.5,
    "funding_scale_enabled": False,
    "universe_mode": "all",
    "universe_top_n": 20,
    "listing_age_days": 90,
    "max_spread_pct": 0.15,
    "drawdown_kill_pct": 0.15,
    "margin_mode": "ISOLATED",
    "risk_per_trade_pct": 0.01,
    "max_symbol_leverage": 20,
    "min_symbol_leverage": 2,
    "max_concurrent_symbols": 8,
    "reserve_margin_buffer_pct": 0.10,
    "chandelier_atr_mult": 3.0,
    "tp1_r_multiple": 1.0,
    "tp2_r_multiple": 2.0,
    "tp1_close_pct": 0.50,
    "tp2_close_pct": 1.0,
    "breakeven_after_tp1": True,
    "breakeven_offset_bps": 10,
}

# ── Aggressive ───────────────────────────────────────────────────
# 수익 극대화. 레버리지 10배, 타이트 손절, 빠른 실행, 넓은 분산.
# 바이낸스 계좌 레버리지: 20배, 실제 노출: 자본의 ~10배.
AGGRESSIVE: dict[str, Any] = {
    "signal_tf": "4h",
    "horizons": [30, 90, 365],
    "deadzone_threshold": 0.05,
    "vol_window": 30,
    "target_portfolio_vol": 0.30,
    "effective_leverage_target": 10.0,
    "stop_atr_window": 10,
    "stop_k": 1.5,
    "trailing_stop": True,
    "execution_tick_sec": 60,
    "execution_threshold_pct": 0.01,
    "entry_order_mode": "MARKET",
    "ioc_epsilon": 0.001,
    "top_k_enabled": True,
    "top_k": 8,
    "replace_threshold": 0.15,
    "min_weight_floor": 0.01,
    "max_weight_cap": 0.30,
    "rsi_period": 14,
    "rsi_overbought": 65.0,
    "rsi_oversold": 35.0,
    "rsi_scale_overbought": 0.7,
    "rsi_scale_oversold": 2.0,
    "funding_scale_enabled": True,
    "universe_mode": "all",
    "universe_top_n": 30,
    "listing_age_days": 60,
    "max_spread_pct": 0.20,
    "drawdown_kill_pct": 0.25,
    "margin_mode": "ISOLATED",
    "risk_per_trade_pct": 0.02,
    "max_symbol_leverage": 25,
    "min_symbol_leverage": 3,
    "max_concurrent_symbols": 12,
    "reserve_margin_buffer_pct": 0.05,
    "chandelier_atr_mult": 2.5,
    "tp1_r_multiple": 1.0,
    "tp2_r_multiple": 1.8,
    "tp1_close_pct": 0.40,
    "tp2_close_pct": 1.0,
    "breakeven_after_tp1": True,
    "breakeven_offset_bps": 5,
}

PROFILES: dict[str, dict[str, Any]] = {
    "conservative": CONSERVATIVE,
    "balanced": BALANCED,
    "aggressive": AGGRESSIVE,
}


def apply_profile(config_dict: dict[str, Any], profile_name: str) -> dict[str, Any]:
    """
    Merge profile defaults into *config_dict* (in-place) and return it.
    Unknown profile names are ignored (treated as ``custom``).
    """
    preset = PROFILES.get(profile_name)
    if preset is None:
        return config_dict
    config_dict.update(preset)
    config_dict["profile"] = profile_name
    return config_dict
