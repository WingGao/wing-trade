"""Ledger domain services.

Database-backed services are loaded lazily so pure Decimal calculations can be
used in tooling that does not install SQLAlchemy.
"""

from .accounting import AccountingService
from .matching import MatchingService
from .target import TargetService

__all__ = [
    "AccountingService",
    "CycleService",
    "DashboardService",
    "MatchingService",
    "TargetService",
    "TradeLedgerService",
]


def __getattr__(name: str):
    """按需加载数据库相关服务，保持纯 Decimal 模块可独立导入。"""
    if name == "CycleService":
        from .cycle import CycleService

        return CycleService
    if name == "DashboardService":
        from .dashboard import DashboardService

        return DashboardService
    if name == "TradeLedgerService":
        from .ledger import TradeLedgerService

        return TradeLedgerService
    raise AttributeError(name)
