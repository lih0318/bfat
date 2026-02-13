"""
Universe selection: filter tradeable USDT-M perpetual symbols.

Pipeline:
  1. exchangeInfo → PERPETUAL + USDT quote + TRADING status
  2. listing_age_days filter (onboardDate)
  3. 24h quoteVolume → top_n
  4. bookTicker → spread_pct filter (max_spread_pct)
  5. liquidity_penalty per symbol
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

from app.services.binance_client import binance_client
from app.services.exchange_info import ExchangeInfoCache

logger = logging.getLogger(__name__)


@dataclass
class UniverseSymbol:
    symbol: str
    quote_volume_24h: float = 0.0
    spread_pct: float = 0.0
    liquidity_penalty: float = 1.0  # 1.0 = no penalty, <1 = penalised


@dataclass
class UniverseResult:
    symbols: list[UniverseSymbol] = field(default_factory=list)
    excluded: list[dict[str, Any]] = field(default_factory=list)
    updated_at: float = 0.0

    @property
    def symbol_names(self) -> list[str]:
        return [s.symbol for s in self.symbols]

    def penalty_map(self) -> dict[str, float]:
        return {s.symbol: s.liquidity_penalty for s in self.symbols}


def get_universe(
    top_n: int = 20,
    listing_age_days: int = 90,
    max_spread_pct: float = 0.15,
) -> UniverseResult:
    """Return filtered universe of tradeable USDT-M perpetual symbols."""

    result = UniverseResult(updated_at=time.time())
    info = ExchangeInfoCache.get()
    now_ms = int(time.time() * 1000)
    min_onboard_ms = now_ms - listing_age_days * 86_400_000

    # Step 1+2: filter symbols from exchangeInfo
    candidates: list[dict[str, Any]] = []
    for sym_info in info.get("symbols", []):
        if sym_info.get("contractType") != "PERPETUAL":
            continue
        if sym_info.get("quoteAsset") != "USDT":
            continue
        if sym_info.get("status") != "TRADING":
            continue
        onboard = sym_info.get("onboardDate", 0)
        if onboard and onboard > min_onboard_ms:
            result.excluded.append({"symbol": sym_info["symbol"], "reason": "too_new"})
            continue
        candidates.append(sym_info)

    if not candidates:
        logger.warning("Universe: no candidates after exchangeInfo filter")
        return result

    # Step 3: 24h quoteVolume → top_n
    try:
        tickers = binance_client.ticker_24hr()
        vol_map: dict[str, float] = {}
        if isinstance(tickers, list):
            for t in tickers:
                vol_map[t.get("symbol", "")] = float(t.get("quoteVolume", 0) or 0)
    except Exception as exc:
        logger.warning("Universe: 24hr ticker failed: %s", exc)
        vol_map = {}

    candidate_symbols = {c["symbol"] for c in candidates}
    ranked = sorted(
        [(s, vol_map.get(s, 0.0)) for s in candidate_symbols],
        key=lambda x: x[1],
        reverse=True,
    )
    top_symbols = ranked[:top_n]

    # Step 4: bookTicker → spread filter
    max_vol = top_symbols[0][1] if top_symbols else 1.0
    for sym, vol24 in top_symbols:
        try:
            bt = binance_client.book_ticker(sym)
            bid = float(bt.get("bidPrice", 0) or 0)
            ask = float(bt.get("askPrice", 0) or 0)
            mid = (bid + ask) / 2.0 if (bid + ask) > 0 else 1.0
            spread = (ask - bid) / mid * 100.0 if mid > 0 else 999.0
        except Exception:
            spread = 999.0
            bid = ask = 0.0

        if spread > max_spread_pct:
            result.excluded.append({"symbol": sym, "reason": f"spread={spread:.4f}%"})
            continue

        # Step 5: liquidity penalty = vol_ratio * (1 - spread_ratio)
        vol_ratio = (vol24 / max_vol) if max_vol > 0 else 1.0
        spread_ratio = spread / max_spread_pct if max_spread_pct > 0 else 0.0
        penalty = max(0.1, vol_ratio * (1.0 - 0.5 * spread_ratio))

        result.symbols.append(UniverseSymbol(
            symbol=sym,
            quote_volume_24h=vol24,
            spread_pct=round(spread, 4),
            liquidity_penalty=round(penalty, 4),
        ))

    logger.info("Universe: %d symbols selected, %d excluded", len(result.symbols), len(result.excluded))
    return result
