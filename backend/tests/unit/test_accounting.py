from decimal import Decimal

from app.domain.models import Fees
from app.services.accounting import AccountingService


def test_buy_cash_out_and_unit_cost() -> None:
    result = AccountingService.calculate_buy("1000", "10", Fees(commission="5"))
    assert result.gross == Decimal("10000.0000")
    assert result.cash_out == Decimal("10005.0000")
    assert result.unit_cost == Decimal("10.005000")


def test_tail_rounding_and_last_lot_cost() -> None:
    first = type("Lot", (), {
        "id": "a", "account_id": "acc", "security_id": "sec", "strategy_id": "s",
        "original_qty": Decimal("3"), "original_cost": Decimal("10.0000"),
        "remaining_qty": Decimal("3"), "remaining_cost": Decimal("10.0000"),
        "executed_at": __import__("datetime").datetime(2026, 1, 1),
    })()
    second = type("Lot", (), {
        "id": "b", "account_id": "acc", "security_id": "sec", "strategy_id": "s",
        "original_qty": Decimal("3"), "original_cost": Decimal("10.0000"),
        "remaining_qty": Decimal("3"), "remaining_cost": Decimal("10.0000"),
        "executed_at": __import__("datetime").datetime(2026, 1, 2),
    })()
    from app.services.matching import MatchingService

    allocations = MatchingService.allocate(
        {"a": first, "b": second}, [("a", "3"), ("b", "1")],
        sell_qty="4", sell_cash_in="13.3333",
    )
    assert sum(item.allocated_sell_net for item in allocations) == Decimal("13.3333")
    assert first.remaining_qty == Decimal("0.0000")


def test_price_diff_pct_handles_normal_and_boundary_cases() -> None:
    """验证价格相对涨跌幅的精确计算与空值/非正数边界。"""
    assert AccountingService.price_diff_pct("248.600", "264.500") == Decimal("-6.011342")
    assert AccountingService.price_diff_pct("75.800", "75.800") == Decimal("0.000000")
    assert AccountingService.price_diff_pct("110", "100") == Decimal("10.000000")
    assert AccountingService.price_diff_pct(None, "100") is None
    assert AccountingService.price_diff_pct("100", None) is None
    assert AccountingService.price_diff_pct("100", "0") is None
    assert AccountingService.price_diff_pct("0", "100") is None
