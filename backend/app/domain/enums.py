from __future__ import annotations

from enum import Enum


class _ValueEnum(str, Enum):
    def __str__(self) -> str:
        return self.value


class TradeSide(_ValueEnum):
    BUY = "BUY"
    SELL = "SELL"


class TradeStatus(_ValueEnum):
    ACTIVE = "ACTIVE"
    VOIDED = "VOIDED"


class TradeSource(_ValueEnum):
    MANUAL = "MANUAL"
    IMPORT = "IMPORT"


class LotStatus(_ValueEnum):
    OPEN = "OPEN"
    CLOSED = "CLOSED"


class CycleStatus(_ValueEnum):
    OPEN = "OPEN"
    CLOSED = "CLOSED"


class TargetStatus(_ValueEnum):
    ACTIVE = "ACTIVE"
    TRIGGERED = "TRIGGERED"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"
    CONFLICT = "CONFLICT"


class CalcBasis(_ValueEnum):
    GROSS = "GROSS"
    NET = "NET"


class MatchingMethod(_ValueEnum):
    FIFO = "FIFO"
    LIFO = "LIFO"
    EQUAL = "EQUAL"
    MANUAL = "MANUAL"


class UserRole(_ValueEnum):
    """用户角色：管理员或普通用户。"""

    ADMIN = "ADMIN"
    USER = "USER"


class UserStatus(_ValueEnum):
    """用户生命周期状态：待激活、正常或已禁用。"""

    INVITED = "INVITED"
    ACTIVE = "ACTIVE"
    DISABLED = "DISABLED"


