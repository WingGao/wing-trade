from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

from app.services.accounting import AccountingService


def _lot(
    identifier: str,
    *,
    price: str,
    qty: str,
    cost: str,
    account: str = "account",
    security: str = "security",
    strategy: str = "strategy",
    status: str = "OPEN",
    trade_status: str = "ACTIVE",
) -> SimpleNamespace:
    trade = SimpleNamespace(
        price=Decimal(price), side="BUY", status=trade_status
    )
    return SimpleNamespace(
        id=identifier,
        account_id=account,
        security_id=security,
        strategy_id=strategy,
        status=status,
        remaining_qty=Decimal(qty),
        remaining_cost=Decimal(cost),
        buy_trade=trade,
    )


def test_lowest_price_group_merges_all_same_price_lots_and_excludes_others() -> None:
    lots = [
        _lot("first", price="8", qty="100", cost="809"),
        _lot("second", price="8", qty="50", cost="401"),
        _lot("higher", price="10", qty="100", cost="1000"),
        _lot("closed", price="8", qty="0", cost="0", status="CLOSED"),
        _lot("voided", price="8", qty="100", cost="800", trade_status="VOIDED"),
        _lot("other-account", price="8", qty="20", cost="160", account="other"),
        _lot("other-security", price="8", qty="20", cost="160", security="other"),
        _lot("other-strategy", price="8", qty="20", cost="160", strategy="other"),
    ]

    metrics = AccountingService.lowest_price_group_metrics(lots, latest_price="10")

    assert metrics is not None
    assert metrics.purchase_price == Decimal("8.000000")
    assert metrics.lot_count == 2
    assert metrics.remaining_qty == Decimal("150.0000")
    assert metrics.remaining_cost == Decimal("1210.0000")
    assert metrics.unrealized_pnl == Decimal("290.0000")
    assert metrics.unrealized_return_pct == Decimal("23.966942")


def test_lowest_price_group_moves_to_next_price_only_after_current_is_gone() -> None:
    lots = [
        _lot("closed", price="8", qty="0", cost="0", status="CLOSED"),
        _lot("voided", price="8", qty="100", cost="800", trade_status="VOIDED"),
        _lot("remaining", price="9", qty="40", cost="370"),
    ]

    metrics = AccountingService.lowest_price_group_metrics(lots, latest_price="10")

    assert metrics is not None
    assert metrics.purchase_price == Decimal("9.000000")
    assert metrics.lot_count == 1
    assert metrics.remaining_qty == Decimal("40.0000")
    assert metrics.unrealized_pnl == Decimal("30.0000")


def test_return_percentages_are_null_for_non_positive_cost() -> None:
    assert AccountingService.return_pct("10", "0") is None
    assert AccountingService.return_pct("10", "-1") is None
    assert AccountingService.return_pct("0", "100") == Decimal("0.000000")
    assert AccountingService.return_pct("-10", "100") == Decimal("-10.000000")
