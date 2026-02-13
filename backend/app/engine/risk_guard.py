"""
Risk guard: drawdown kill switch, portfolio exposure limits, per-symbol leverage check.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class RiskCheckResult:
    ok: bool = True
    kill: bool = False  # True → engine must stop
    reason: str = ""
    warnings: list[str] | None = None


def check_drawdown(
    current_equity: float,
    peak_equity: float,
    kill_pct: float = 0.10,
) -> RiskCheckResult:
    """
    Kill switch: if drawdown from peak exceeds ``kill_pct``, signal engine stop.

    Returns RiskCheckResult with kill=True if triggered.
    """
    if peak_equity <= 0 or current_equity <= 0:
        return RiskCheckResult(ok=True)

    drawdown = (peak_equity - current_equity) / peak_equity

    if drawdown >= kill_pct:
        return RiskCheckResult(
            ok=False,
            kill=True,
            reason=f"Drawdown {drawdown:.2%} exceeds kill threshold {kill_pct:.2%} "
                   f"(equity={current_equity:.2f}, peak={peak_equity:.2f})",
        )

    # Warn at 50% of kill threshold
    if drawdown >= kill_pct * 0.5:
        return RiskCheckResult(
            ok=True,
            warnings=[
                f"Drawdown warning: {drawdown:.2%} (kill at {kill_pct:.2%})"
            ],
        )

    return RiskCheckResult(ok=True)


def check_portfolio_exposure(
    positions: list[dict[str, Any]],
    equity: float,
    max_gross_leverage: float = 3.0,
) -> RiskCheckResult:
    """
    Check gross portfolio exposure does not exceed max_gross_leverage × equity.

    Parameters
    ----------
    positions : list of position dicts with 'notional' key
    equity : total wallet balance
    max_gross_leverage : maximum allowed gross leverage ratio
    """
    if equity <= 0:
        return RiskCheckResult(ok=True)

    gross_notional = sum(abs(float(p.get("notional", 0) or 0)) for p in positions)
    gross_leverage = gross_notional / equity

    if gross_leverage > max_gross_leverage:
        return RiskCheckResult(
            ok=False,
            reason=f"Gross leverage {gross_leverage:.2f}x exceeds limit {max_gross_leverage:.2f}x "
                   f"(gross_notional={gross_notional:.2f}, equity={equity:.2f})",
        )

    if gross_leverage > max_gross_leverage * 0.8:
        return RiskCheckResult(
            ok=True,
            warnings=[
                f"Leverage warning: {gross_leverage:.2f}x (limit: {max_gross_leverage:.2f}x)"
            ],
        )

    return RiskCheckResult(ok=True)


def check_per_symbol_leverage(
    symbol: str,
    position_notional: float,
    equity: float,
    max_symbol_leverage: float = 5.0,
) -> RiskCheckResult:
    """Check if a single symbol's leverage is within limits."""
    if equity <= 0:
        return RiskCheckResult(ok=True)

    sym_leverage = abs(position_notional) / equity

    if sym_leverage > max_symbol_leverage:
        return RiskCheckResult(
            ok=False,
            reason=f"{symbol}: leverage {sym_leverage:.2f}x exceeds {max_symbol_leverage:.2f}x",
        )

    return RiskCheckResult(ok=True)


def run_all_checks(
    current_equity: float,
    peak_equity: float,
    positions: list[dict[str, Any]],
    kill_pct: float = 0.10,
    max_gross_leverage: float = 3.0,
) -> RiskCheckResult:
    """Run all risk checks and return the most severe result."""
    warnings: list[str] = []

    # Drawdown check
    dd = check_drawdown(current_equity, peak_equity, kill_pct)
    if dd.kill:
        return dd
    if dd.warnings:
        warnings.extend(dd.warnings)

    # Portfolio exposure
    exp = check_portfolio_exposure(positions, current_equity, max_gross_leverage)
    if not exp.ok:
        exp.warnings = warnings + (exp.warnings or [])
        return exp
    if exp.warnings:
        warnings.extend(exp.warnings)

    result = RiskCheckResult(ok=True)
    if warnings:
        result.warnings = warnings
    return result
