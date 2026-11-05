from decimal import Decimal

from app.services.target import TargetService


def test_net_target_price_honours_minimum_commission() -> None:
    price = TargetService.solve_price(
        unit_cost="10",
        planned_sell_qty="100",
        target_return_pct="0.10",
        commission_rate="0.0003",
        minimum_commission="5",
    )
    gross = price * Decimal("100")
    net = gross - max(gross * Decimal("0.0003"), Decimal("5"))
    assert net >= Decimal("1100.0000")
    assert price > Decimal("11")
