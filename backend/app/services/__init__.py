"""Ledger domain services.

Database-backed services are loaded lazily so pure Decimal calculations can be
used in tooling that does not install SQLAlchemy.
"""

from .accounting import AccountingService
from .matching import MatchingService
from .target import TargetService

__all__ = ["AccountingService", "CycleService", "MatchingService", "TargetService", "TradeLedgerService"]


def __getattr__(name: str):
    if name == "CycleService":
        from .cycle import CycleService

        return CycleService
    if name == "TradeLedgerService":
        from .ledger import TradeLedgerService

        return TradeLedgerService
    raise AttributeError(name)
