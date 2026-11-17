from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.domain.exceptions import InsufficientLotQtyError, ValidationError
from app.services.matching import MatchingService


def _lot(
    identifier: str,
    *,
    qty: str,
    executed_at: datetime,
    created_at: datetime | None = None,
    status: str = "OPEN",
    account_id: str = "account",
    security_id: str = "security",
    strategy_id: str = "strategy",
) -> SimpleNamespace:
    """构造自动配对测试所需的最小批次对象。"""
    remaining = Decimal(qty)
    return SimpleNamespace(
        id=identifier,
        account_id=account_id,
        security_id=security_id,
        strategy_id=strategy_id,
        original_qty=remaining,
        original_cost=remaining * Decimal("10"),
        remaining_qty=remaining,
        remaining_cost=remaining * Decimal("10"),
        executed_at=executed_at,
        created_at=created_at or executed_at,
        status=status,
    )


def test_lifo_preview_uses_newest_lot_then_previous_lot() -> None:
    """最近买入优先应跨批次补足卖出数量。"""
    older = _lot("older", qty="10", executed_at=datetime(2026, 11, 1, tzinfo=UTC))
    newer = _lot("newer", qty="4", executed_at=datetime(2026, 11, 2, tzinfo=UTC))

    requests = MatchingService.lifo_preview([older, newer], "6")

    assert [(request.lot_id, request.matched_qty) for request in requests] == [
        ("newer", Decimal("4.0000")),
        ("older", Decimal("2.0000")),
    ]


def test_lifo_preview_is_deterministic_for_equal_execution_time() -> None:
    """相同买入时间应按创建时间和编号稳定地从新到旧排序。"""
    first_at = datetime(2026, 11, 2, tzinfo=UTC)
    second_at = datetime(2026, 11, 2, 1, tzinfo=UTC)
    first = _lot("a", qty="2", executed_at=first_at, created_at=first_at)
    second = _lot("b", qty="2", executed_at=first_at, created_at=second_at)

    requests = MatchingService.lifo_preview([first, second], "3")

    assert [(request.lot_id, request.matched_qty) for request in requests] == [
        ("b", Decimal("2.0000")),
        ("a", Decimal("1.0000")),
    ]


def test_lifo_preview_excludes_ineligible_lots_and_reports_shortfall() -> None:
    """已结清、范围不符、零数量和未来买入不得参与自动配对。"""
    sell_at = datetime(2026, 11, 5, tzinfo=UTC)
    eligible = _lot("eligible", qty="3", executed_at=datetime(2026, 11, 1, tzinfo=UTC))
    future = _lot("future", qty="10", executed_at=datetime(2026, 11, 6, tzinfo=UTC))
    closed = _lot(
        "closed",
        qty="10",
        executed_at=datetime(2026, 11, 2, tzinfo=UTC),
        status="CLOSED",
    )
    empty = _lot("empty", qty="0", executed_at=datetime(2026, 11, 3, tzinfo=UTC))
    other_account = _lot(
        "other-account",
        qty="10",
        executed_at=datetime(2026, 11, 4, tzinfo=UTC),
        account_id="other",
    )
    other_security = _lot(
        "other-security",
        qty="10",
        executed_at=datetime(2026, 11, 4, 1, tzinfo=UTC),
        security_id="other-security",
    )
    other_strategy = _lot(
        "other-strategy",
        qty="10",
        executed_at=datetime(2026, 11, 4, 2, tzinfo=UTC),
        strategy_id="other-strategy",
    )
    lots = [eligible, future, closed, empty, other_account, other_security, other_strategy]

    requests = MatchingService.auto_preview(
        lots,
        "3",
        method="LIFO",
        sell_executed_at=sell_at,
        account_id="account",
        security_id="security",
        strategy_id="strategy",
    )
    assert [request.lot_id for request in requests] == ["eligible"]

    with pytest.raises(InsufficientLotQtyError):
        MatchingService.auto_preview(
            lots,
            "4",
            method="LIFO",
            sell_executed_at=sell_at,
            account_id="account",
            security_id="security",
            strategy_id="strategy",
        )

    with pytest.raises(ValidationError):
        MatchingService.auto_preview(lots, "1", method="MANUAL")


def test_fifo_preview_remains_the_compatible_default() -> None:
    """未指定新策略时仍按最早买入优先配对。"""
    older = _lot("older", qty="2", executed_at=datetime(2026, 11, 1, tzinfo=UTC))
    newer = _lot("newer", qty="2", executed_at=datetime(2026, 11, 2, tzinfo=UTC))

    requests = MatchingService.auto_preview([newer, older], "1")

    assert requests[0].lot_id == "older"
