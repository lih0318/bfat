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


def check_available_balance(
    available_balance: float,
    equity: float,
    reserve_buffer_pct: float = 0.10,
) -> RiskCheckResult:
    """
    Ensure available balance (after open positions' margin) exceeds the reserve buffer.
    Prevents new entries when margin is depleted.
    """
    if equity <= 0:
        return RiskCheckResult(ok=True)
    reserve = equity * reserve_buffer_pct
    if available_balance < reserve:
        return RiskCheckResult(
            ok=False,
            reason=f"Available balance {available_balance:.2f} below reserve "
                   f"{reserve:.2f} ({reserve_buffer_pct:.0%} of equity {equity:.2f})",
        )
    if available_balance < reserve * 1.5:
        return RiskCheckResult(
            ok=True,
            warnings=[
                f"Available balance low: {available_balance:.2f} "
                f"(reserve={reserve:.2f})"
            ],
        )
    return RiskCheckResult(ok=True)


def check_concurrent_symbols(
    open_symbol_count: int,
    max_concurrent: int = 10,
) -> RiskCheckResult:
    """Block new entries if max concurrent symbols reached."""
    if open_symbol_count >= max_concurrent:
        return RiskCheckResult(
            ok=False,
            reason=f"Concurrent symbols {open_symbol_count} >= limit {max_concurrent}",
        )
    return RiskCheckResult(ok=True)


def run_all_checks(
    current_equity: float,
    peak_equity: float,
    positions: list[dict[str, Any]],
    kill_pct: float = 0.10,
    max_gross_leverage: float = 3.0,
    available_balance: float = 0.0,
    reserve_buffer_pct: float = 0.10,
    max_concurrent_symbols: int = 10,
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

    # Available balance / reserve buffer
    if available_balance > 0:
        ab = check_available_balance(available_balance, current_equity, reserve_buffer_pct)
        if not ab.ok:
            ab.warnings = warnings + (ab.warnings or [])
            return ab
        if ab.warnings:
            warnings.extend(ab.warnings)

    # Concurrent symbols
    open_count = len([p for p in positions if float(p.get("positionAmt", 0)) != 0])
    cs = check_concurrent_symbols(open_count, max_concurrent_symbols)
    if not cs.ok:
        cs.warnings = warnings + (cs.warnings or [])
        return cs

    result = RiskCheckResult(ok=True)
    if warnings:
        result.warnings = warnings
    return result
