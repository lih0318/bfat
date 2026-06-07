"""
Exchange Info cache for symbol filters (min notional, step size, tick size).
Used before placing orders to validate and round price/quantity.
"""
import time
from typing import Any

from app.services.binance_client import binance_client


class ExchangeInfoCache:
    _cache: dict[str, Any] | None = None
    _ts: float = 0
    TTL_SEC = 3600

    @classmethod
    def get(cls) -> dict[str, Any]:
        now = time.monotonic()
        if cls._cache is None or (now - cls._ts) > cls.TTL_SEC:
            cls._cache = binance_client.exchange_info()
            cls._ts = now
        return cls._cache

    @classmethod
    def get_symbol_filters(cls, symbol: str) -> dict[str, Any]:
        info = cls.get()
        for s in info.get("symbols", []):
            if s.get("symbol") == symbol.upper():
                filters = {}
                for f in s.get("filters", []):
                    ft = f.get("filterType")
                    if ft == "PRICE_FILTER":
                        filters["tick_size"] = float(f.get("tickSize", 0.01))
                        filters["min_price"] = float(f.get("minPrice", 0))
                        filters["max_price"] = float(f.get("maxPrice", 0))
                    elif ft == "LOT_SIZE":
                        filters["step_size"] = float(f.get("stepSize", 0.001))
                        filters["min_qty"] = float(f.get("minQty", 0))
                        filters["max_qty"] = float(f.get("maxQty", 0))
                    elif ft == "MIN_NOTIONAL":
                        filters["min_notional"] = float(f.get("notional", 0))
                return filters
        return {}

    @classmethod
    def round_quantity(cls, symbol: str, qty: float) -> float:
        f = cls.get_symbol_filters(symbol)
        step = f.get("step_size", 0.001)
        if step <= 0:
            return qty
        import math
        precision = max(0, -int(round(math.log10(step))))
        return round(qty - (qty % step), precision)

    @classmethod
    def round_price(cls, symbol: str, price: float) -> float:
        f = cls.get_symbol_filters(symbol)
        tick = f.get("tick_size", 0.01)
        if tick <= 0:
            return price
        import math
        precision = max(0, -int(round(math.log10(tick))))
        return round(price - (price % tick), precision)

    @classmethod
    def check_min_notional(cls, symbol: str, qty: float, price: float) -> bool:
        f = cls.get_symbol_filters(symbol)
        min_n = f.get("min_notional", 0)
        return (qty * price) >= min_n
