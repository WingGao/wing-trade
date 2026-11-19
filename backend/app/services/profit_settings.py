"""全局、个股与批次止盈设置的只读继承投影（按用户维度隔离）。"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.models.ledger import Account, Lot, ProfitSetting
from app.services.auth_bootstrap import profit_setting_scope_key
from app.services.target import TargetService


class ProfitSettingsService:
    """解析止盈来源，不在读取时生成或更改批次目标。"""

    @staticmethod
    def scope_key(
        security_id: str | None = None,
        *,
        user_id: str | None = None,
    ) -> str:
        """返回按用户隔离的全局或证券唯一配置键；缺省 user_id 保留旧键兼容。"""
        if user_id:
            return profit_setting_scope_key(user_id, security_id)
        return f"SECURITY:{security_id}" if security_id else "GLOBAL"

    @classmethod
    def effective_target(cls, session: Session, lot: Lot) -> dict[str, Any] | None:
        """按批次、用户个股、用户全局优先级为持有中批次解析当前展示值。"""
        if lot.status != "OPEN" or lot.remaining_qty <= 0:
            return None
        explicit = [
            target
            for target in lot.targets
            if target.status in {"ACTIVE", "TRIGGERED"} and target.planned_sell_qty > 0
        ]
        if explicit:
            target = sorted(explicit, key=lambda item: (item.created_at, item.id))[-1]
            return {
                "id": target.id,
                "source": "LOT",
                "targetReturnPct": f"{target.target_return_pct:.6f}",
                "calculationBasis": target.calculation_basis,
                "targetPriceEstimate": (
                    f"{target.target_price_estimate:.6f}"
                    if target.target_price_estimate is not None
                    else None
                ),
                "plannedSellQty": f"{target.planned_sell_qty:.4f}",
                "alertEnabled": target.alert_enabled,
            }
        account = session.get(Account, lot.account_id)
        owner_user_id = account.user_id if account is not None else None
        setting = session.get(
            ProfitSetting, cls.scope_key(lot.security_id, user_id=owner_user_id)
        )
        source = "SECURITY"
        if setting is None:
            setting = session.get(ProfitSetting, cls.scope_key(user_id=owner_user_id))
            source = "GLOBAL"
        if setting is None:
            return None
        price = TargetService.solve_price(
            unit_cost=lot.unit_cost,
            planned_sell_qty=lot.remaining_qty,
            target_return_pct=setting.target_return_pct,
            basis=setting.calculation_basis,
        )
        return {
            "source": source,
            "targetReturnPct": f"{setting.target_return_pct:.6f}",
            "calculationBasis": setting.calculation_basis,
            "targetPriceEstimate": f"{price:.6f}",
            "plannedSellQty": f"{lot.remaining_qty:.4f}",
            "alertEnabled": setting.alert_enabled,
        }
