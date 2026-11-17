from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime
from decimal import Decimal

from app.domain.enums import MatchingMethod
from app.domain.exceptions import (
    FutureLotError,
    InsufficientLotQtyError,
    InvalidQuantityError,
    LotOwnershipError,
    MatchQuantityMismatchError,
    ValidationError,
)
from app.domain.models import MatchAllocation, MatchRequest
from app.domain.precision import round_amount, round_qty
from app.services.accounting import AccountingService


def _value(obj: object, name: str, default: object | None = None) -> object | None:
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _as_utc(value: datetime) -> datetime:
    """将时间统一转换为 UTC，兼容 SQLite 返回的无时区值。"""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _lot_sort_key(lot: object) -> tuple[datetime, datetime, str]:
    """生成买入批次在自动配对中的确定性排序键。"""
    executed_at = _value(lot, "executed_at")
    created_at = _value(lot, "created_at")
    missing_time = datetime.min.replace(tzinfo=UTC)
    return (
        _as_utc(executed_at) if isinstance(executed_at, datetime) else missing_time,
        _as_utc(created_at) if isinstance(created_at, datetime) else missing_time,
        str(_value(lot, "id", "")),
    )


class MatchingService:
    """提供自动配对预览和确定性的买卖分摊。"""

    @staticmethod
    def _eligible_lots(
        lots: Sequence[object],
        *,
        sell_executed_at: datetime | None = None,
        account_id: str | None = None,
        security_id: str | None = None,
        strategy_id: str | None = None,
    ) -> list[object]:
        """过滤可参与自动配对的批次，排除范围、状态和时间不符的数据。"""
        sell_at = _as_utc(sell_executed_at) if sell_executed_at is not None else None
        eligible: list[object] = []
        for lot in lots:
            if round_qty(_value(lot, "remaining_qty", 0)) <= 0:
                continue
            if str(_value(lot, "status", "OPEN")) != "OPEN":
                continue
            if account_id is not None and _value(lot, "account_id") != account_id:
                continue
            if security_id is not None and _value(lot, "security_id") != security_id:
                continue
            if strategy_id is not None and _value(lot, "strategy_id") != strategy_id:
                continue
            lot_executed_at = _value(lot, "executed_at")
            if (
                sell_at is not None
                and isinstance(lot_executed_at, datetime)
                and _as_utc(lot_executed_at) > sell_at
            ):
                continue
            eligible.append(lot)
        return eligible

    @classmethod
    def auto_preview(
        cls,
        lots: Sequence[object],
        sell_qty: object,
        *,
        method: MatchingMethod | str = MatchingMethod.FIFO,
        sell_executed_at: datetime | None = None,
        account_id: str | None = None,
        security_id: str | None = None,
        strategy_id: str | None = None,
    ) -> list[MatchRequest]:
        """按指定自动策略生成完整卖出量对应的批次请求。"""
        quantity = round_qty(sell_qty)
        if quantity <= 0:
            raise InvalidQuantityError("sell quantity must be positive")
        try:
            matching_method = MatchingMethod(method)
        except ValueError as exc:
            raise ValidationError("matching method must be FIFO or LIFO") from exc
        if matching_method not in {MatchingMethod.FIFO, MatchingMethod.LIFO}:
            raise ValidationError("automatic matching requires FIFO or LIFO")
        eligible = cls._eligible_lots(
            lots,
            sell_executed_at=sell_executed_at,
            account_id=account_id,
            security_id=security_id,
            strategy_id=strategy_id,
        )
        ordered = sorted(
            eligible,
            key=_lot_sort_key,
            reverse=matching_method == MatchingMethod.LIFO,
        )
        result: list[MatchRequest] = []
        left = quantity
        for lot in ordered:
            if left <= 0:
                break
            available = round_qty(_value(lot, "remaining_qty", 0))
            take = min(available, left)
            result.append(MatchRequest(str(_value(lot, "id")), take))
            left = round_qty(left - take)
        if left > 0:
            raise InsufficientLotQtyError(
                "available lots cannot cover sell quantity",
                details={"requested": str(quantity), "unmatched": str(left)},
            )
        return result

    @classmethod
    def fifo_preview(
        cls,
        lots: Sequence[object],
        sell_qty: object,
        *,
        sell_executed_at: datetime | None = None,
    ) -> list[MatchRequest]:
        """保留最早买入优先的兼容入口。"""
        return cls.auto_preview(
            lots,
            sell_qty,
            method=MatchingMethod.FIFO,
            sell_executed_at=sell_executed_at,
        )

    @classmethod
    def lifo_preview(
        cls,
        lots: Sequence[object],
        sell_qty: object,
        *,
        sell_executed_at: datetime | None = None,
    ) -> list[MatchRequest]:
        """按买入时间、创建时间和编号从新到旧生成配对请求。"""
        return cls.auto_preview(
            lots,
            sell_qty,
            method=MatchingMethod.LIFO,
            sell_executed_at=sell_executed_at,
        )

    preview_fifo = fifo_preview
    preview_lifo = lifo_preview

    @staticmethod
    def normalize_requests(
        requests: Iterable[MatchRequest | dict[str, object] | tuple[str, object]],
    ) -> list[MatchRequest]:
        """将多种批次请求格式统一为四舍五入后的领域对象。"""
        normalized: list[MatchRequest] = []
        for request in requests:
            if isinstance(request, MatchRequest):
                normalized.append(request)
            elif isinstance(request, dict):
                normalized.append(
                    MatchRequest(
                        str(request["lot_id"]),
                        round_qty(request.get("matched_qty", request.get("qty"))),
                    )
                )
            else:
                normalized.append(MatchRequest(str(request[0]), round_qty(request[1])))
        return normalized

    @classmethod
    def validate_manual_matches(
        cls,
        lots_by_id: Mapping[str, object],
        requests: Iterable[MatchRequest | dict[str, object] | tuple[str, object]],
        *,
        sell_qty: object,
        account_id: str | None = None,
        security_id: str | None = None,
        strategy_id: str | None = None,
        sell_executed_at: datetime | None = None,
    ) -> list[MatchRequest]:
        normalized = cls.normalize_requests(requests)
        expected = round_qty(sell_qty)
        total = Decimal("0")
        seen: set[str] = set()
        for request in normalized:
            if request.lot_id in seen:
                raise MatchQuantityMismatchError("the same lot cannot appear more than once")
            seen.add(request.lot_id)
            if request.matched_qty <= 0:
                raise InvalidQuantityError("matched quantity must be positive")
            lot = lots_by_id.get(request.lot_id)
            if lot is None:
                raise InsufficientLotQtyError(f"lot {request.lot_id} does not exist")
            if account_id is not None and _value(lot, "account_id") != account_id:
                raise LotOwnershipError("lot belongs to another account")
            if security_id is not None and _value(lot, "security_id") != security_id:
                raise LotOwnershipError("lot belongs to another security")
            if strategy_id is not None and _value(lot, "strategy_id") != strategy_id:
                raise LotOwnershipError("lot belongs to another strategy")
            lot_executed_at = _value(lot, "executed_at")
            if (
                sell_executed_at is not None
                and isinstance(lot_executed_at, datetime)
                and _as_utc(lot_executed_at) > _as_utc(sell_executed_at)
            ):
                raise FutureLotError("a lot cannot be sold before it was bought")
            available = round_qty(_value(lot, "remaining_qty", 0))
            if request.matched_qty > available:
                raise InsufficientLotQtyError(
                    f"lot {request.lot_id} has insufficient quantity",
                    details={"available": str(available), "requested": str(request.matched_qty)},
                )
            total = round_qty(total + request.matched_qty)
        if total != expected:
            raise MatchQuantityMismatchError(
                "matched quantity must equal sell quantity",
                details={"matched": str(total), "sell": str(expected)},
            )
        return normalized

    @classmethod
    def allocate(
        cls,
        lots_by_id: Mapping[str, object],
        requests: Iterable[MatchRequest | dict[str, object] | tuple[str, object]],
        *,
        sell_qty: object,
        sell_cash_in: object,
        method: MatchingMethod = MatchingMethod.MANUAL,
        account_id: str | None = None,
        security_id: str | None = None,
        strategy_id: str | None = None,
        sell_executed_at: datetime | None = None,
    ) -> list[MatchAllocation]:
        """校验明确批次并按原始剩余成本计算逐批分摊。"""
        normalized = cls.validate_manual_matches(
            lots_by_id,
            requests,
            sell_qty=sell_qty,
            account_id=account_id,
            security_id=security_id,
            strategy_id=strategy_id,
            sell_executed_at=sell_executed_at,
        )
        total_qty = round_qty(sell_qty)
        cash_in = round_amount(sell_cash_in)
        allocations: list[MatchAllocation] = []
        allocated_net = Decimal("0.0000")
        for index, request in enumerate(normalized):
            lot = lots_by_id[request.lot_id]
            remaining_qty = round_qty(_value(lot, "remaining_qty", 0))
            remaining_cost = round_amount(_value(lot, "remaining_cost", 0))
            original_qty = round_qty(_value(lot, "original_qty", remaining_qty))
            original_cost = round_amount(_value(lot, "original_cost", remaining_cost))
            cost = AccountingService.allocate_buy_cost(
                original_qty, original_cost, remaining_qty, request.matched_qty, remaining_cost
            )
            if index == len(normalized) - 1:
                sell_net = round_amount(cash_in - allocated_net)
            else:
                sell_net = AccountingService.allocate_sell_net(
                    cash_in, total_qty, request.matched_qty
                )
            allocated_net = round_amount(allocated_net + sell_net)
            new_qty = round_qty(remaining_qty - request.matched_qty)
            new_cost = round_amount(remaining_cost - cost)
            if hasattr(lot, "remaining_qty"):
                setattr(lot, "remaining_qty", new_qty)
                setattr(lot, "remaining_cost", new_cost)
                if hasattr(lot, "status"):
                    setattr(lot, "status", "CLOSED" if new_qty == 0 else "OPEN")
            allocations.append(
                MatchAllocation(
                    lot_id=request.lot_id,
                    matched_qty=request.matched_qty,
                    allocated_buy_cost=cost,
                    allocated_sell_net=sell_net,
                    realized_pnl=round_amount(sell_net - cost),
                    method=method,
                )
            )
        return allocations

    match = allocate

