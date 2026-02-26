"""BFAT persistence module."""

from app.core.database import DatabaseFactory
from app.persistence.trade_log import TradeRepository
from app.persistence.equity_log import EquityRepository
from app.persistence.system_log import SystemLogRepository


def create_persistence(db_factory: DatabaseFactory) -> tuple[TradeRepository, EquityRepository, SystemLogRepository]:
    """Create repository instances from a database factory."""
    return (
        TradeRepository(db_factory),
        EquityRepository(db_factory),
        SystemLogRepository(db_factory),
    )
