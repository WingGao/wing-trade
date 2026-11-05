from __future__ import annotations

from decimal import Decimal, ROUND_CEILING
from typing import Iterable

from app.domain.enums import CalcBasis, TargetStatus
from app.domain.exceptions import InvalidQuantityError, ValidationError
from app.domain.models import TargetPriceInput
from app.domain.precision import ensure_decimal, round_amount, round_price, round_qty


class TargetService:
    """Profit-target validation and deterministic price solving."""

    @staticmethod
    def _field(target: object, name: str, default: object | None = None) -> object | None:
        if isinstance(target, dict):
            return target.get(name, default)
        return getattr(target, name, default)

    @staticmethod
    def _non_negative(value: object, field: str) -> Decimal:
        result = ensure_decimal(value, field=field)
        if result < 0:
            raise ValidationError(f"{field} must not be negative")
        return result

    @classmethod
    def solve_price(
        cls,
        *,
        unit_cost: object,
        planned_sell_qty: object,
        target_return_pct: object,
        basis: CalcBasis | str = CalcBasis.NET,
        commission_rate: object = Decimal("0"),
        minimum_commission: object = Decimal("0"),
        stamp_duty_rate: object = Decimal("0"),
        transfer_fee_rate: object = Decimal("0"),
    ) -> Decimal:
        cost = cls._non_negative(unit_cost, "unit_cost")
        quantity = round_qty(planned_sell_qty)
        target_return = cls._non_negative(target_return_pct, "target_return_pct")
        commission = cls._non_negative(commission_rate, "commission_rate")
        minimum = cls._non_negative(minimum_commission, "minimum_commission")
        stamp = cls._non_negative(stamp_duty_rate, "stamp_duty_rate")
        transfer = cls._non_negative(transfer_fee_rate, "transfer_fee_rate")
        if quantity <= 0:
            raise InvalidQuantityError("planned sell quantity must be positive")
        basis_value = CalcBasis(basis)
        target_amount = cost * quantity * (Decimal("1") + target_return)
        if basis_value == CalcBasis.GROSS:
            return round_price(target_amount / quantity)

        other_rate = stamp + transfer
        target_amount = round_amount(target_amount)
        denominator_prop = Decimal("1") - commission - other_rate
        denominator_min = Decimal("1") - other_rate
        if denominator_prop <= 0 or denominator_min <= 0:
            raise ValidationError("fee rates leave no positive net proceeds")
        proportional_gross = target_amount / denominator_prop
        if proportional_gross * commission >= minimum:
            candidate = proportional_gross / quantity
        else:
            candidate = (target_amount + minimum) / denominator_min / quantity
        # Round upward so a displayed target never undershoots the requested
        # net return after fixed-scale price quantization.
        quantum = Decimal("0.000001")
        price = candidate.quantize(quantum, rounding=ROUND_CEILING)
        for _ in range(3):
            gross = price * quantity
            commission_fee = max(gross * commission, minimum)
            net = round_amount(gross - commission_fee - gross * other_rate)
            if net >= target_amount:
                return round_price(price)
            price = (price + quantum).quantize(quantum)
        return round_price(price)

    solve_net_price = solve_price

    @classmethod
    def target_price_from_input(cls, data: TargetPriceInput) -> Decimal:
        return cls.solve_price(
            unit_cost=data.unit_cost,
            planned_sell_qty=data.planned_sell_qty,
            target_return_pct=data.target_return_pct,
            basis=data.basis,
            commission_rate=data.commission_rate,
            minimum_commission=data.minimum_commission,
            stamp_duty_rate=data.stamp_duty_rate,
            transfer_fee_rate=data.transfer_fee_rate,
        )

    @staticmethod
    def validate_planned_quantity(targets: Iterable[object], remaining_qty: object) -> Decimal:
        available = round_qty(remaining_qty)
        total = round_qty(
            sum((round_qty(getattr(target, "planned_sell_qty", target["planned_sell_qty"] if isinstance(target, dict) else 0)) for target in targets), Decimal("0"))
        )
        if total > available:
            raise ValidationError(
                "planned target quantity exceeds lot remaining quantity",
                details={"planned": str(total), "remaining": str(available)},
            )
        return total

    @staticmethod
    def mark_conflicts(targets: Iterable[object], remaining_qty: object) -> int:
        remaining = round_qty(remaining_qty)
        total = Decimal("0")
        conflicts = 0
        for target in targets:
            quantity = round_qty(TargetService._field(target, "planned_sell_qty", 0))
            total += quantity
            if total > remaining and TargetService._field(target, "status") == TargetStatus.ACTIVE.value:
                if isinstance(target, dict):
                    target["status"] = TargetStatus.CONFLICT.value
                else:
                    setattr(target, "status", TargetStatus.CONFLICT.value)
                conflicts += 1
        return conflicts

