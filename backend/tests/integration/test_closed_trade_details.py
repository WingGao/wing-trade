"""在隔离内存库中验证已结清买卖明细，禁止改写交易或派生账务。"""

from __future__ import annotations

from collections.abc import Generator
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, select
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.api.schemas.position import SettledTradeDetailRead
from app.db.base import Base
from app.db.session import create_engine_for_url
from app.domain.models import Fees, MatchRequest
from app.main import create_app
from app.models.ledger import Account, Lot, QuoteCache, Security, Strategy, Trade, TradeMatch
from app.services.ledger import TradeLedgerService


@dataclass(frozen=True)
class Scope:
    """测试交易必须显式携带的账户、证券和策略范围。"""

    account_id: str
    security_id: str
    strategy_id: str


@pytest.fixture
def settled_context() -> Generator[tuple[Session, Scope], None, None]:
    """创建内存账本及明确默认范围，测试不会连接用户本地数据库。"""
    engine = create_engine_for_url("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as session:
        account = Account(name="结清明细测试", currency="CNY")
        security = Security(market="SH", symbol="600519", name="贵州茅台")
        session.add_all([account, security])
        session.flush()
        strategy = Strategy(account_id=account.id, name="DEFAULT")
        session.add(strategy)
        from app.config import DEFAULT_DEV_ADMIN_EMAIL
        from app.services.auth_bootstrap import ensure_default_admin_and_backfill

        ensure_default_admin_and_backfill(session, DEFAULT_DEV_ADMIN_EMAIL)
        session.commit()
        yield session, Scope(account.id, security.id, strategy.id)
    engine.dispose()


def _client(session: Session) -> TestClient:
    """将测试 API 的会话依赖指向同一隔离内存数据库。"""
    from sqlalchemy import select

    from app.api.deps import get_current_user
    from app.models.auth import User

    application = create_app(create_tables=False)
    admin = session.scalar(select(User).where(User.is_default_admin.is_(True)).limit(1))
    assert admin is not None

    def override_db() -> Generator[Session, None, None]:
        """由夹具管理会话生命周期，避免访问应用默认数据库。"""
        yield session

    application.dependency_overrides[get_db] = override_db
    application.dependency_overrides[get_current_user] = lambda: admin
    return TestClient(application)


def _buy(
    session: Session,
    scope: Scope,
    *,
    day: int,
    qty: str,
    price: str,
    fee: str = "0",
) -> Trade:
    """创建包含真实原价、原量和买入费用的交易事实。"""
    return TradeLedgerService(session).buy(
        account_id=scope.account_id,
        security_id=scope.security_id,
        strategy_id=scope.strategy_id,
        executed_at=datetime(2026, 11, day, tzinfo=UTC),
        qty=qty,
        price=price,
        fees=Fees(commission=Decimal(fee)),
    )


def _sell(
    session: Session,
    scope: Scope,
    *,
    day: int,
    qty: str,
    price: str,
    matches: list[MatchRequest] | None = None,
    fee: str = "0",
) -> Trade:
    """创建含明确批次分摊或默认 FIFO 的卖出事实。"""
    return TradeLedgerService(session).sell(
        account_id=scope.account_id,
        security_id=scope.security_id,
        strategy_id=scope.strategy_id,
        executed_at=datetime(2026, 11, day, tzinfo=UTC),
        qty=qty,
        price=price,
        matches=matches,
        fees=Fees(commission=Decimal(fee)),
    )


def _url(scope: Scope, *, include_closed: bool) -> str:
    """显式传递全部范围和开关，避免隐式账户创建干扰只读断言。"""
    return (
        f"/api/v1/securities/{scope.security_id}/lots?accountId={scope.account_id}"
        f"&strategyId={scope.strategy_id}&includeClosed={str(include_closed).lower()}"
    )


def _database_rows(session: Session) -> dict[str, list[tuple[Any, ...]]]:
    """记录所有表的实际列值，验证 GET 连更新时间和审计也没有修改。"""
    return {
        table.name: sorted(
            (tuple(row) for row in session.execute(select(table))),
            key=repr,
        )
        for table in Base.metadata.sorted_tables
    }


def test_closed_details_include_every_active_buy_and_partial_sell_without_double_counting(
    settled_context,
) -> None:
    """多次部分卖出与跨批成交保留全部闭仓历史，仅计本批数量/回款/利润。"""
    session, scope = settled_context
    first = _buy(session, scope, day=1, qty="10", price="10.123", fee="1.23")
    second = _buy(session, scope, day=2, qty="6", price="11.456", fee="0.54")
    first_lot = first.lot
    second_lot = second.lot
    first_lot_id, second_lot_id = first_lot.id, second_lot.id
    # 已作废卖出仍保留匹配审计资料，不能混入有效买卖明细。
    voided_sell = _sell(
        session,
        scope,
        day=3,
        qty="2",
        price="99.999",
        matches=[
            MatchRequest(first_lot_id, Decimal("1")),
            MatchRequest(second_lot_id, Decimal("1")),
        ],
    )
    TradeLedgerService(session).void_trade(voided_sell.id)
    partial = _sell(
        session,
        scope,
        day=4,
        qty="4",
        price="12.345",
        fee="0.12",
        matches=[
            MatchRequest(first_lot_id, Decimal("3")),
            MatchRequest(second_lot_id, Decimal("1")),
        ],
    )
    final = _sell(
        session,
        scope,
        day=5,
        qty="12",
        price="12.789",
        fee="0.19",
        matches=[
            MatchRequest(first_lot_id, Decimal("7")),
            MatchRequest(second_lot_id, Decimal("5")),
        ],
    )
    next_buy = _buy(session, scope, day=7, qty="3", price="13.001", fee="0.03")
    next_sell = _sell(session, scope, day=8, qty="3", price="14.002", fee="0.02")
    still_open = _buy(session, scope, day=9, qty="2", price="15.123")
    voided_buy = _buy(session, scope, day=10, qty="1", price="16.123")
    TradeLedgerService(session).void_trade(voided_buy.id)
    # 原价和含费现金额必须源自事实，即使缓存行情或派生成本不同也不能反推。
    first_lot.unit_cost = Decimal("999")
    session.add(
        QuoteCache(
            security_id=scope.security_id,
            last_price=Decimal("888"),
            provider="unrelated",
            fetched_at=datetime.now(UTC),
            is_stale=True,
        )
    )
    session.commit()
    session.expire_all()
    before = _database_rows(session)
    mutations: list[str] = []

    def capture_writes(_conn, _cursor, statement, _parameters, _context, _many) -> None:
        """只捕获读取期间的真正写 SQL，避免行数不变但更新字段的漏检。"""
        if statement.lstrip().split(maxsplit=1)[0].upper() in {"INSERT", "UPDATE", "DELETE"}:
            mutations.append(statement)

    engine = session.get_bind()
    event.listen(engine, "before_cursor_execute", capture_writes)
    try:
        with _client(session) as client:
            hidden = client.get(_url(scope, include_closed=False))
            shown = client.get(_url(scope, include_closed=True))
            repeated = client.get(_url(scope, include_closed=True))
    finally:
        event.remove(engine, "before_cursor_execute", capture_writes)
    assert hidden.status_code == shown.status_code == repeated.status_code == 200
    assert hidden.json()["closedTradeDetails"] == []
    body = shown.json()
    rows = body["closedTradeDetails"]
    assert rows == repeated.json()["closedTradeDetails"]
    assert len(rows) == 8
    assert {row["tradeId"] for row in rows} == {
        first.id,
        second.id,
        partial.id,
        final.id,
        next_buy.id,
        next_sell.id,
    }
    assert not ({voided_sell.id, voided_buy.id, still_open.id} & {row["tradeId"] for row in rows})
    assert {row["lotId"] for row in rows} == {first_lot_id, second_lot_id, next_buy.lot.id}
    expected_order = sorted(
        rows,
        key=lambda row: (row["executedAt"], row["tradeId"], row["lotId"], row["side"]),
        reverse=True,
    )
    assert rows == expected_order
    assert all(row["executedAt"].endswith("+00:00") for row in rows)
    for row in rows:
        SettledTradeDetailRead.model_validate(row)
        for field in ("qty", "originalTradeQty", "price", "cashAmount"):
            assert isinstance(row[field], str)
    buys = {row["tradeId"]: row for row in rows if row["side"] == "BUY"}
    assert buys[first.id]["price"] == "10.123000"
    assert buys[first.id]["qty"] == buys[first.id]["originalTradeQty"] == "10.0000"
    assert buys[first.id]["cashAmount"] == "102.4600"
    assert buys[second.id]["cashAmount"] == "69.2760"
    assert all(row["realizedPnl"] is None for row in buys.values())
    for trade in (partial, final, next_sell):
        details = [row for row in rows if row["tradeId"] == trade.id]
        matches = {
            match.lot_id: match
            for match in session.scalars(
                select(TradeMatch).where(TradeMatch.sell_trade_id == trade.id)
            )
        }
        assert sum((Decimal(row["qty"]) for row in details), Decimal("0")) == trade.qty
        assert sum((Decimal(row["cashAmount"]) for row in details), Decimal("0")) == (
            trade.gross_amount - trade.fees_total
        )
        for row in details:
            allocation = matches[row["lotId"]]
            assert row["originalTradeQty"] == f"{trade.qty:.4f}"
            assert row["price"] == f"{trade.price:.6f}"
            assert row["qty"] == f"{allocation.matched_qty:.4f}"
            assert row["cashAmount"] == f"{allocation.allocated_sell_net:.4f}"
            assert row["realizedPnl"] == f"{allocation.realized_pnl:.4f}"
    assert body["realizedPnl"] == hidden.json()["realizedPnl"]
    assert body["closedLotCount"] == hidden.json()["closedLotCount"]
    assert mutations == []
    assert _database_rows(session) == before


def test_closed_details_are_isolated_by_account_security_and_strategy(settled_context) -> None:
    """相同证券的其他账户/策略及相同账户的其他证券不能混入明细。"""
    session, scope = settled_context
    owner = session.get(Account, scope.account_id)
    account = Account(
        user_id=owner.user_id if owner is not None else None,
        name="其他账户",
        currency="CNY",
    )
    security = Security(market="SZ", symbol="000001", name="平安银行")
    session.add_all([account, security])
    session.flush()
    account_strategy = Strategy(account_id=account.id, name="DEFAULT")
    other_strategy = Strategy(account_id=scope.account_id, name="其他策略")
    session.add_all([account_strategy, other_strategy])
    session.commit()
    scopes = [
        scope,
        Scope(account.id, scope.security_id, account_strategy.id),
        Scope(scope.account_id, security.id, scope.strategy_id),
        Scope(scope.account_id, scope.security_id, other_strategy.id),
    ]
    expected = {}
    for current in scopes:
        buy = _buy(session, current, day=1, qty="5", price="10.001", fee="0.01")
        sell = _sell(session, current, day=2, qty="5", price="11.002", fee="0.02")
        expected[current] = (buy.lot.id, {buy.id, sell.id})
    with _client(session) as client:
        for current in scopes:
            result = client.get(_url(current, include_closed=True))
            assert result.status_code == 200
            rows = result.json()["closedTradeDetails"]
            lot_id, trade_ids = expected[current]
            assert len(rows) == 2
            assert {row["lotId"] for row in rows} == {lot_id}
            assert {row["tradeId"] for row in rows} == trade_ids
            assert (
                client.get(_url(current, include_closed=False)).json()["closedTradeDetails"] == []
            )


def test_cross_lot_sale_does_not_repeat_open_lot_quantity_or_cash(settled_context) -> None:
    """一笔卖出只结清其中一批时，闭仓明细不能重复整笔数量和净回款。"""
    session, scope = settled_context
    closed_buy = _buy(session, scope, day=1, qty="2", price="10.001", fee="0.02")
    open_buy = _buy(session, scope, day=2, qty="10", price="11.002", fee="0.03")
    sell = _sell(
        session,
        scope,
        day=3,
        qty="5",
        price="12.003",
        fee="0.05",
        matches=[
            MatchRequest(closed_buy.lot.id, Decimal("2")),
            MatchRequest(open_buy.lot.id, Decimal("3")),
        ],
    )
    closed_id = closed_buy.lot.id
    assert session.get(Lot, open_buy.lot.id).status == "OPEN"
    with _client(session) as client:
        rows = client.get(_url(scope, include_closed=True)).json()["closedTradeDetails"]
    assert len(rows) == 2
    sold = next(row for row in rows if row["side"] == "SELL")
    assert sold["tradeId"] == sell.id
    assert sold["lotId"] == closed_id
    assert sold["qty"] == "2.0000"
    assert sold["originalTradeQty"] == "5.0000"
    assert sold["cashAmount"] == "23.9860"
    assert sold["realizedPnl"] == "3.9640"
