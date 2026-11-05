from __future__ import annotations

from decimal import Decimal

from pydantic import Field

from .common import APIModel


class ProfitTargetCreate(APIModel):
    planned_sell_qty: Decimal
    target_return_pct: Decimal
    calculation_basis: str = "NET"
    commission_rate: Decimal = Decimal("0")
    minimum_commission: Decimal = Decimal("0")
    stamp_duty_rate: Decimal = Decimal("0")
    transfer_fee_rate: Decimal = Decimal("0")
    alert_enabled: bool = True


class ProfitTargetPatch(APIModel):
    planned_sell_qty: Decimal | None = None
    target_return_pct: Decimal | None = None
    calculation_basis: str | None = None
    alert_enabled: bool | None = None
    commission_rate: Decimal | None = None
    minimum_commission: Decimal | None = None
    stamp_duty_rate: Decimal | None = None
    transfer_fee_rate: Decimal | None = None
    status: str | None = None


class ProfitTargetRead(APIModel):
    id: str
    lot_id: str
    target_return_pct: Decimal
    planned_sell_qty: Decimal
    calculation_basis: str
    target_price_estimate: Decimal | None = None
    alert_enabled: bool
    status: str
    triggered_at: object | None = None
    created_at: object

