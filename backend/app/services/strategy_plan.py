"""个股买卖策略表（用户 + 标的维度）的持久化与业务校验服务。"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.domain.enums import StrategyRowStatus
from app.domain.exceptions import EntityNotFoundError, ValidationError
from app.domain.precision import ensure_decimal, round_price, round_qty
from app.models.ledger import Security, SecurityStrategyRow


class StrategyPlanService:
    """管理用户维度的个股买卖策略档位表，支持查询、批量导入、单行维护与清空。"""

    def __init__(self, session: Session) -> None:
        """初始化策略表服务并绑定当前数据库会话。"""
        self.session = session

    def _require_security(self, security_id: str) -> Security:
        """校验证券存在性，不存在时抛出 404 领域错误。"""
        security = self.session.get(Security, security_id)
        if security is None:
            raise EntityNotFoundError("证券不存在", details={"security_id": security_id})
        return security

    def _require_row(self, user_id: str, security_id: str, row_id: str) -> SecurityStrategyRow:
        """校验策略行存在且归属于指定用户与标的，越权或缺失统一返回 404。"""
        row = self.session.scalar(
            select(SecurityStrategyRow).where(
                SecurityStrategyRow.id == row_id,
                SecurityStrategyRow.user_id == user_id,
                SecurityStrategyRow.security_id == security_id,
            )
        )
        if row is None:
            raise EntityNotFoundError("策略档位不存在", details={"row_id": row_id})
        return row

    @staticmethod
    def _normalize_positive_decimal(
        value: Decimal | None,
        *,
        field: str,
        is_price: bool,
    ) -> Decimal | None:
        """将可选价格或数量规整为高精度正数，非法值抛出校验错误。"""
        if value is None:
            return None
        parsed = ensure_decimal(value, field=field)
        if parsed <= 0:
            raise ValidationError(f"{field} 必须大于 0", details={"field": field})
        return round_price(parsed) if is_price else round_qty(parsed)

    @staticmethod
    def _normalize_note(note: str | None) -> str | None:
        """去除备注首尾空白，空字符串归一化为 None 并校验长度上限。"""
        if note is None:
            return None
        trimmed = note.strip()
        if not trimmed:
            return None
        if len(trimmed) > 256:
            raise ValidationError("备注长度不能超过 256 个字符", details={"field": "note"})
        return trimmed

    @staticmethod
    def _validate_non_empty_row(
        *,
        buy_price: Decimal | None,
        buy_qty: Decimal | None,
        sell_price: Decimal | None,
        sell_qty: Decimal | None,
    ) -> None:
        """校验单条策略档位至少包含一个有效的买入或卖出数值，禁止全空行。"""
        if buy_price is None and buy_qty is None and sell_price is None and sell_qty is None:
            raise ValidationError("策略档位至少需填写一项买入或卖出的价格/数量")

    def list_rows(self, user_id: str, security_id: str) -> list[SecurityStrategyRow]:
        """按展示顺序读取指定用户在某标的下的全部策略档位。"""
        self._require_security(security_id)
        stmt = (
            select(SecurityStrategyRow)
            .where(
                SecurityStrategyRow.user_id == user_id,
                SecurityStrategyRow.security_id == security_id,
            )
            .order_by(SecurityStrategyRow.sort_order.asc(), SecurityStrategyRow.created_at.asc())
        )
        return list(self.session.scalars(stmt))

    def import_rows(
        self,
        user_id: str,
        security_id: str,
        rows_data: Sequence[dict[str, Any]],
        *,
        mode: str = "replace",
    ) -> list[SecurityStrategyRow]:
        """按覆盖或追加模式批量写入策略档位并返回最新完整列表。"""
        self._require_security(security_id)
        if not rows_data:
            raise ValidationError("导入的策略档位不能为空")
        if mode not in {"replace", "append"}:
            raise ValidationError("不支持的导入模式", details={"mode": mode})

        normalized_items: list[dict[str, Any]] = []
        for index, raw in enumerate(rows_data, start=1):
            buy_price = self._normalize_positive_decimal(
                raw.get("buy_price"), field="buyPrice", is_price=True
            )
            buy_qty = self._normalize_positive_decimal(
                raw.get("buy_qty"), field="buyQty", is_price=False
            )
            sell_price = self._normalize_positive_decimal(
                raw.get("sell_price"), field="sellPrice", is_price=True
            )
            sell_qty = self._normalize_positive_decimal(
                raw.get("sell_qty"), field="sellQty", is_price=False
            )
            try:
                self._validate_non_empty_row(
                    buy_price=buy_price,
                    buy_qty=buy_qty,
                    sell_price=sell_price,
                    sell_qty=sell_qty,
                )
            except ValidationError as exc:
                raise ValidationError(
                    f"第 {index} 行至少需填写一项买入或卖出的价格/数量",
                    details={"row": index},
                ) from exc
            status_val = raw.get("status") or StrategyRowStatus.PENDING
            status_enum = (
                status_val
                if isinstance(status_val, StrategyRowStatus)
                else StrategyRowStatus(str(status_val))
            )
            normalized_items.append(
                {
                    "buy_price": buy_price,
                    "buy_qty": buy_qty,
                    "sell_price": sell_price,
                    "sell_qty": sell_qty,
                    "note": self._normalize_note(raw.get("note")),
                    "status": status_enum.value,
                }
            )

        if mode == "replace":
            self.session.execute(
                delete(SecurityStrategyRow).where(
                    SecurityStrategyRow.user_id == user_id,
                    SecurityStrategyRow.security_id == security_id,
                )
            )
            start_order = 0
        else:
            current_max = self.session.scalar(
                select(func.max(SecurityStrategyRow.sort_order)).where(
                    SecurityStrategyRow.user_id == user_id,
                    SecurityStrategyRow.security_id == security_id,
                )
            )
            start_order = 0 if current_max is None else int(current_max) + 1

        for offset, item in enumerate(normalized_items):
            self.session.add(
                SecurityStrategyRow(
                    user_id=user_id,
                    security_id=security_id,
                    sort_order=start_order + offset,
                    **item,
                )
            )
        self.session.flush()
        return self.list_rows(user_id, security_id)

    def create_row(
        self,
        user_id: str,
        security_id: str,
        *,
        buy_price: Decimal | None = None,
        buy_qty: Decimal | None = None,
        sell_price: Decimal | None = None,
        sell_qty: Decimal | None = None,
        note: str | None = None,
        status: StrategyRowStatus = StrategyRowStatus.PENDING,
    ) -> SecurityStrategyRow:
        """在当前标的策略表末尾新增一条档位记录。"""
        self._require_security(security_id)
        norm_buy_price = self._normalize_positive_decimal(buy_price, field="buyPrice", is_price=True)
        norm_buy_qty = self._normalize_positive_decimal(buy_qty, field="buyQty", is_price=False)
        norm_sell_price = self._normalize_positive_decimal(sell_price, field="sellPrice", is_price=True)
        norm_sell_qty = self._normalize_positive_decimal(sell_qty, field="sellQty", is_price=False)
        self._validate_non_empty_row(
            buy_price=norm_buy_price,
            buy_qty=norm_buy_qty,
            sell_price=norm_sell_price,
            sell_qty=norm_sell_qty,
        )
        current_max = self.session.scalar(
            select(func.max(SecurityStrategyRow.sort_order)).where(
                SecurityStrategyRow.user_id == user_id,
                SecurityStrategyRow.security_id == security_id,
            )
        )
        next_order = 0 if current_max is None else int(current_max) + 1
        row = SecurityStrategyRow(
            user_id=user_id,
            security_id=security_id,
            sort_order=next_order,
            buy_price=norm_buy_price,
            buy_qty=norm_buy_qty,
            sell_price=norm_sell_price,
            sell_qty=norm_sell_qty,
            note=self._normalize_note(note),
            status=status.value,
        )
        self.session.add(row)
        self.session.flush()
        return row

    def update_row(
        self,
        user_id: str,
        security_id: str,
        row_id: str,
        *,
        updates: dict[str, Any],
    ) -> SecurityStrategyRow:
        """局部更新单条策略档位字段并确保更新后仍满足非全空与正数约束。"""
        self._require_security(security_id)
        row = self._require_row(user_id, security_id, row_id)

        next_buy_price = (
            self._normalize_positive_decimal(updates["buy_price"], field="buyPrice", is_price=True)
            if "buy_price" in updates
            else row.buy_price
        )
        next_buy_qty = (
            self._normalize_positive_decimal(updates["buy_qty"], field="buyQty", is_price=False)
            if "buy_qty" in updates
            else row.buy_qty
        )
        next_sell_price = (
            self._normalize_positive_decimal(updates["sell_price"], field="sellPrice", is_price=True)
            if "sell_price" in updates
            else row.sell_price
        )
        next_sell_qty = (
            self._normalize_positive_decimal(updates["sell_qty"], field="sellQty", is_price=False)
            if "sell_qty" in updates
            else row.sell_qty
        )
        self._validate_non_empty_row(
            buy_price=next_buy_price,
            buy_qty=next_buy_qty,
            sell_price=next_sell_price,
            sell_qty=next_sell_qty,
        )

        row.buy_price = next_buy_price
        row.buy_qty = next_buy_qty
        row.sell_price = next_sell_price
        row.sell_qty = next_sell_qty
        if "note" in updates:
            row.note = self._normalize_note(updates["note"])
        if "status" in updates and updates["status"] is not None:
            status_val = updates["status"]
            row.status = (
                status_val.value
                if isinstance(status_val, StrategyRowStatus)
                else StrategyRowStatus(str(status_val)).value
            )
        if "sort_order" in updates and updates["sort_order"] is not None:
            row.sort_order = int(updates["sort_order"])

        self.session.flush()
        return row

    def delete_row(self, user_id: str, security_id: str, row_id: str) -> None:
        """删除当前用户指定标的下的单条策略档位。"""
        self._require_security(security_id)
        row = self._require_row(user_id, security_id, row_id)
        self.session.delete(row)
        self.session.flush()

    def clear_rows(self, user_id: str, security_id: str) -> int:
        """清空当前用户指定标的下的全部策略档位并返回删除行数。"""
        self._require_security(security_id)
        result = self.session.execute(
            delete(SecurityStrategyRow).where(
                SecurityStrategyRow.user_id == user_id,
                SecurityStrategyRow.security_id == security_id,
            )
        )
        self.session.flush()
        return int(result.rowcount or 0)
