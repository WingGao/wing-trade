from decimal import Decimal
from datetime import datetime, timezone

from app.services.matching import MatchingService


def _lot(identifier: str, cost: str, day: int):
    return type("Lot", (), {
        "id": identifier, "account_id": "acc", "security_id": "sec", "strategy_id": "default",
        "original_qty": Decimal("1000"), "original_cost": Decimal(cost),
        "remaining_qty": Decimal("1000"), "remaining_cost": Decimal(cost),
        "executed_at": datetime(2026, 11, day, tzinfo=timezone.utc),
    })()


def test_spec_case_lot_a_is_closed_and_realized_profit_is_1000() -> None:
    lot_a = _lot("a", "10000", 1)
    lot_b = _lot("b", "9000", 3)
    allocations = MatchingService.allocate(
        {"a": lot_a, "b": lot_b}, [("a", "1000")],
        sell_qty="1000", sell_cash_in="11000",
    )
    assert lot_a.remaining_qty == Decimal("0.0000")
    assert allocations[0].realized_pnl == Decimal("1000.0000")
    assert lot_b.remaining_cost == Decimal("9000.0000")
