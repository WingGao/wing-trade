from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import datetime, timezone
from decimal import Decimal

from app.domain.enums import MatchingMethod
from app.domain.exceptions import (
    FutureLotError,
    InsufficientLotQtyError,
    LotOwnershipError,
    MatchQuantityMismatchError,
    InvalidQuantityError,
)
from app.domain.models import MatchAllocation, MatchRequest
from app.domain.precision import round_amount, round_qty
from app.services.accounting import AccountingService


def _value(obj: object, name: str, default: object | None = None) -> object | None:
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


class MatchingService:
    """FIFO preview and deterministic buy/sell allocation."""

    @staticmethod
    def fifo_preview(lots: Sequence[object], sell_qty: object) -> list[MatchRequest]:
        quantity = round_qty(sell_qty)
        if quantity <= 0:
            raise InvalidQuantityError("sell quantity must be positive")
        ordered = sorted(
            (lot for lot in lots if round_qty(_value(lot, "remaining_qty", 0)) > 0),
            key=lambda lot: (
                _value(lot, "executed_at"),
                _value(lot, "created_at") or datetime.min.replace(tzinfo=timezone.utc),
                str(_value(lot, "id", "")),
            ),
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

    preview_fifo = fifo_preview

    @staticmethod
    def normalize_requests(requests: Iterable[MatchRequest | dict[str, object] | tuple[str, object]]) -> list[MatchRequest]:
        normalized: list[MatchRequest] = []
        for request in requests:
            if isinstance(request, MatchRequest):
                normalized.append(request)
            elif isinstance(request, dict):
                normalized.append(MatchRequest(str(request["lot_id"]), request.get("matched_qty", request.get("qty"))))
            else:
                normalized.append(MatchRequest(str(request[0]), request[1]))
        return normalized

    @classmethod
    def validate_manual_matches(
        cls,
        lots_by_id: dict[str, object],
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
        lots_by_id: dict[str, object],
        requests: Iterable[MatchRequest | dict[str, object] | tuple[str, object]],
        *,
        sell_qty: object,
        sell_cash_in: object,
        method: MatchingMethod = MatchingMethod.MANUAL,
        **scope: object,
    ) -> list[MatchAllocation]:
        normalized = cls.validate_manual_matches(lots_by_id, requests, sell_qty=sell_qty, **scope)
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
                sell_net = AccountingService.allocate_sell_net(cash_in, total_qty, request.matched_qty)
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

