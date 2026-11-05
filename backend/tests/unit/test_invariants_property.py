from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

pytest.importorskip("hypothesis")
from hypothesis import given, strategies as st

from app.services.matching import MatchingService


@st.composite
def allocation_cases(draw) -> tuple[int, int, int, int]:
    first_qty = draw(st.integers(min_value=1, max_value=2_000))
    second_qty = draw(st.integers(min_value=1, max_value=2_000))
    first_match = draw(st.integers(min_value=1, max_value=first_qty))
    second_match = draw(st.integers(min_value=1, max_value=second_qty))
    return first_qty, second_qty, first_match, second_match


@given(allocation_cases())
def test_matching_preserves_tail_net_and_lot_costs(case: tuple[int, int, int, int]) -> None:
    first_qty, second_qty, first_match, second_match = case
    first_cost = Decimal(first_qty) * Decimal("10.0000")
    second_cost = Decimal(second_qty) * Decimal("9.0000")
    first = SimpleNamespace(
        id="first",
        account_id="account",
        security_id="security",
        strategy_id="strategy",
        original_qty=Decimal(first_qty),
        original_cost=first_cost,
        remaining_qty=Decimal(first_qty),
        remaining_cost=first_cost,
        executed_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    second = SimpleNamespace(
        id="second",
        account_id="account",
        security_id="security",
        strategy_id="strategy",
        original_qty=Decimal(second_qty),
        original_cost=second_cost,
        remaining_qty=Decimal(second_qty),
        remaining_cost=second_cost,
        executed_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
    )
    sell_cash_in = Decimal("1234.5678")
    allocations = MatchingService.allocate(
        {"first": first, "second": second},
        [("first", first_match), ("second", second_match)],
        sell_qty=first_match + second_match,
        sell_cash_in=sell_cash_in,
    )

    assert sum(item.matched_qty for item in allocations) == Decimal(first_match + second_match)
    assert sum(item.allocated_sell_net for item in allocations) == sell_cash_in
    assert first.remaining_cost + allocations[0].allocated_buy_cost == first_cost
    assert second.remaining_cost + allocations[1].allocated_buy_cost == second_cost
