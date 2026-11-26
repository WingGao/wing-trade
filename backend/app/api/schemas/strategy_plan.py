"""个股策略表请求与响应 Pydantic 模型。"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import Field

from app.domain.enums import StrategyRowStatus

from .common import APIModel


class StrategyRowInput(APIModel):
    """单条策略档位的创建或批量导入输入模型。"""

    buy_price: Decimal | None = None
    buy_qty: Decimal | None = None
    sell_price: Decimal | None = None
    sell_qty: Decimal | None = None
    note: str | None = Field(default=None, max_length=256)
    status: StrategyRowStatus = StrategyRowStatus.PENDING


class StrategyRowUpdate(APIModel):
    """单条策略档位的局部更新请求模型。"""

    buy_price: Decimal | None = None
    buy_qty: Decimal | None = None
    sell_price: Decimal | None = None
    sell_qty: Decimal | None = None
    note: str | None = Field(default=None, max_length=256)
    status: StrategyRowStatus | None = None
    sort_order: int | None = None


class StrategyRowsImportRequest(APIModel):
    """批量导入策略档位请求模型，支持覆盖替换或追加末尾。"""

    mode: Literal["replace", "append"] = "replace"
    rows: list[StrategyRowInput] = Field(min_length=1, max_length=200)


class StrategyRowRead(APIModel):
    """个股策略表档位响应模型。"""

    id: str
    security_id: str
    sort_order: int
    buy_price: str | None = None
    buy_qty: str | None = None
    sell_price: str | None = None
    sell_qty: str | None = None
    note: str | None = None
    status: StrategyRowStatus
    created_at: datetime
    updated_at: datetime
