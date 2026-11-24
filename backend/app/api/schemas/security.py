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
    security_id: str
    sort_order: int = 0
    account_id: str | None = None


class WatchlistItemRead(APIModel):
    id: str
    account_id: str
    security_id: str
    sort_order: int
    security: SecurityRead | None = None

