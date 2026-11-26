from __future__ import annotations

from decimal import Decimal

from pydantic import Field

from .common import APIModel


class SecurityCreate(APIModel):
    """创建公共证券资料请求模型。"""

    market: str = Field(min_length=1, max_length=8)
    symbol: str = Field(min_length=1, max_length=32)
    name: str | None = None
    security_type: str = "STOCK"


class SecurityProxyUpdate(APIModel):
    """绑定、修改或清除场外基金盘中估算参考标的请求模型。"""

    proxy_symbol: str | None = Field(default=None, max_length=32)
    proxy_name: str | None = Field(default=None, max_length=128)
    tracking_ratio: Decimal | None = None


class SecurityRead(APIModel):
    """公共证券资料响应模型，包含可选的盘中估算参考标的元数据。"""

    id: str
    market: str
    symbol: str
    name: str | None = None
    security_type: str
    status: str
    currency: str | None = None
    proxy_symbol: str | None = None
    proxy_name: str | None = None
    tracking_ratio: Decimal | None = None


class WatchlistItemCreate(APIModel):
    """新增或幂等更新关注证券请求模型。"""

    security_id: str
    sort_order: int = 0
    account_id: str | None = None
    target_buy_price: Decimal | None = None
    alert_enabled: bool = False
    note: str | None = Field(default=None, max_length=256)


class WatchlistItemUpdate(APIModel):
    """局部更新关注证券目标买回价、提醒开关与备注请求模型。"""

    sort_order: int | None = None
    target_buy_price: Decimal | None = None
    alert_enabled: bool | None = None
    note: str | None = Field(default=None, max_length=256)


class WatchlistItemRead(APIModel):
    """关注证券基础响应模型。"""

    id: str
    account_id: str
    security_id: str
    sort_order: int
    target_buy_price: Decimal | None = None
    alert_enabled: bool = False
    note: str | None = None
    security: SecurityRead | None = None

