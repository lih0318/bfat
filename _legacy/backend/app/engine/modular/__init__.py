"""
Modular engine: signal -> risk -> execution -> pos_mgmt -> perf_log -> optimizer (periodic).
All inter-module communication via dataclass/dict.
Binance API called only from execution_engine.
"""
from app.engine.modular.risk_engine import RiskContext
from app.engine.modular.runner import ModularRunner
from app.engine.modular.types import (
    ConfigUpdate,
    ExecutionResult,
    MarketSnapshot,
    OrderPlan,
    PerformanceLog,
    TradeRecord,
    RiskDecision,
    RiskResult,
    SignalOutput,
    SignalResult,
)

__all__ = [
    "ConfigUpdate",
    "RiskContext",
    "ExecutionResult",
    "MarketSnapshot",
    "ModularRunner",
    "OrderPlan",
    "PerformanceLog",
    "TradeRecord",
    "RiskDecision",
    "RiskResult",
    "SignalOutput",
    "SignalResult",
]
