"""批次估值、止盈继承与重建在隔离 SQLite 中的行为回归。"""

from __future__ import annotations

import asyncio
from collections.abc import Generator
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.api.utils import begin_write
from app.db.base import Base
from app.db.session import create_engine_for_url
from app.domain.exceptions import InsufficientLotQtyError
from app.main import create_app
from app.market_data import MockQuoteProvider, QuoteCollector, QuoteSnapshot
from app.models.ledger import (
    Account,
    AuditEvent,
    Cycle,
    Lot,
    ProfitTarget,
    QuoteCache,
    Security,
    Strategy,
    Trade,
    TradeMatch,
)
from app.services.dashboard import DashboardService
from app.services.ledger import TradeLedgerService
from app.services.recalculate import RecalculationService


@pytest.fixture
def ledger_context() -> Generator[tuple[Session, Account, Security, Strategy], None, None]:
    """为每个测试新建内存库，不接触项目中的真实账本。"""
    engine = create_engine_for_url("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as session:
        account = Account(name="测试账户", currency="CNY")
        security = Security(market="SH", symbol="600519", name="贵州茅台")
        session.add_all([account, security])
        session.flush()
        strategy = Strategy(account_id=account.id, name="DEFAULT")
        session.add(strategy)
        session.commit()
        yield session, account, security, strategy
    engine.dispose()


def _buy(
    context: tuple[Session, Account, Security, Strategy],
    *,
    day: int,
    qty: str,
    price: str = "10",
) -> Trade:
    """创建具有可核对交易锚点的测试买入。"""
    session, account, security, strategy = context
    return TradeLedgerService(session).buy(
        account_id=account.id,
        security_id=security.id,
        strategy_id=strategy.id,
        executed_at=datetime(2026, 11, day, tzinfo=UTC),
        qty=qty,
        price=price,
        client_request_id=f"buy-{security.id}-{strategy.id}-{day}",
    )


def _sell(context: tuple[Session, Account, Security, Strategy], *, day: int, qty: str) -> Trade:
    """创建用于验证均分和利润归属的卖出。"""
    session, account, security, strategy = context
    return TradeLedgerService(session).sell(
        account_id=account.id,
        security_id=security.id,
        strategy_id=strategy.id,
        executed_at=datetime(2026, 11, day, tzinfo=UTC),
        qty=qty,
        price="12",
        matching_method="EQUAL",
    )


def _api(session: Session) -> TestClient:
    """将 HTTP 依赖指向当前隔离数据库。"""
    application = create_app(create_tables=False)

    def override_db() -> Generator[Session, None, None]:
        """复用测试会话，让 HTTP 与数据库断言检查同一隔离状态。"""
        yield session

    application.dependency_overrides[get_db] = override_db
    return TestClient(application)


def test_profit_setting_inheritance_reads_without_materializing_targets(ledger_context) -> None:
    """三层覆盖逐级生效；清空批次和个股覆盖后恢复继承，GET 不生成目标。"""
    session, account, security, strategy = ledger_context
    buy = _buy(ledger_context, day=1, qty="100")
    lot_id = buy.lot.id
    with _api(session) as client:
        global_result = client.put("/api/v1/profit-settings", json={"targetReturnPct": "0.1"})
        assert global_result.status_code == 200
        url = f"/api/v1/securities/{security.id}/lots?accountId={account.id}"
        inherited = client.get(url).json()["lots"][0]
        assert inherited["effectiveTarget"]["source"] == "GLOBAL"
        assert Decimal(inherited["effectiveTarget"]["targetPriceEstimate"]) == Decimal("11")
        assert session.scalar(select(func.count()).select_from(ProfitTarget)) == 0
        response = client.put(
            f"/api/v1/securities/{security.id}/profit-settings",
            json={"targetReturnPct": "0.2", "calculationBasis": "GROSS"},
        )
        assert response.status_code == 200
        assert client.get(url).json()["lots"][0]["effectiveTarget"]["source"] == "SECURITY"
        target = client.post(
            f"/api/v1/lots/{lot_id}/targets",
            json={
                "targetReturnPct": "0.3",
                "plannedSellQty": "100",
            },
        )
        assert target.status_code == 201
        assert client.get(url).json()["lots"][0]["effectiveTarget"]["source"] == "LOT"
        client.delete(f"/api/v1/targets/{target.json()['id']}")
        assert client.get(url).json()["lots"][0]["effectiveTarget"]["source"] == "SECURITY"
        # 已取消的完整目标不能阻止再次为全部剩余股数设置目标。
        replacement = client.post(
            f"/api/v1/lots/{lot_id}/targets",
            json={
                "targetReturnPct": "0.4",
                "plannedSellQty": "100",
            },
        )
        assert replacement.status_code == 201
        client.delete(f"/api/v1/targets/{replacement.json()['id']}")
        client.put(
            f"/api/v1/securities/{security.id}/profit-settings", json={"targetReturnPct": None}
        )
        assert client.get(url).json()["lots"][0]["effectiveTarget"]["source"] == "GLOBAL"
        assert session.scalar(select(func.count()).select_from(ProfitTarget)) == 2
        assert (
            client.put("/api/v1/profit-settings", json={"targetReturnPct": "-0.1"}).status_code
            == 422
        )


def test_lots_include_partial_sale_pnl_quote_and_descending_order(ledger_context) -> None:
    """未结清批次部分卖出的差价也计入个股收益，浮盈只计算剩余持仓。"""
    session, account, security, _ = ledger_context
    first = _buy(ledger_context, day=1, qty="10")
    second = _buy(ledger_context, day=2, qty="4", price="11")
    _sell(ledger_context, day=3, qty="6")
    session.add(
        QuoteCache(
            security_id=security.id,
            last_price=Decimal("12"),
            provider="test",
            fetched_at=datetime.now(UTC),
            is_stale=False,
        )
    )
    session.commit()
    with _api(session) as client:
        result = client.get(f"/api/v1/securities/{security.id}/lots?accountId={account.id}")
        assert result.status_code == 200
        body = result.json()
        assert [lot["buyTradeId"] for lot in body["lots"]] == [second.id, first.id]
        assert Decimal(body["realizedPnl"]) == Decimal("9")
        assert body["quote"]["provider"] == "test"
        assert Decimal(body["lots"][0]["unrealizedPnl"]) == Decimal("1")
        assert Decimal(body["lots"][1]["unrealizedPnl"]) == Decimal("14")
        trades = client.get("/api/v1/trades").json()
        assert trades[0]["side"] == "SELL"
        assert trades[0]["symbol"] == "600519"
        assert trades[0]["name"] == "贵州茅台"


def _snapshot(session: Session) -> tuple[list, list, list]:
    """捕获派生数值与稳定编号，忽略预期追加的审计事件。"""
    lots = sorted(
        (lot.id, lot.remaining_qty, lot.remaining_cost, lot.cycle_id)
        for lot in session.scalars(select(Lot))
    )
    matches = sorted(
        (match.id, match.lot_id, match.matched_qty, match.realized_pnl)
        for match in session.scalars(select(TradeMatch))
    )
    cycles = sorted(
        (cycle.id, cycle.status, cycle.opened_at, cycle.closed_at)
        for cycle in session.scalars(select(Cycle))
    )
    return lots, matches, cycles


def test_rebuild_preserves_facts_targets_other_scopes_and_is_repeatable(ledger_context) -> None:
    """重建修复损坏投影、保留事实和止盈，并且不影响其他个股/策略。"""
    session, account, security, strategy = ledger_context
    first = _buy(ledger_context, day=1, qty="10")
    second = _buy(ledger_context, day=2, qty="4", price="11")
    _sell(ledger_context, day=3, qty="6")
    target = ProfitTarget(
        lot_id=first.lot.id,
        target_return_pct=Decimal("0.1"),
        planned_sell_qty=Decimal("1"),
        calculation_basis="NET",
        status="ACTIVE",
    )
    other_security = Security(market="SZ", symbol="000001", name="平安银行")
    other_strategy = Strategy(account_id=account.id, name="其他策略")
    session.add_all([target, other_security, other_strategy])
    session.flush()
    _buy((session, account, other_security, strategy), day=1, qty="2")
    _buy((session, account, security, other_strategy), day=1, qty="3")
    session.commit()
    original_trades = {
        trade.id: (
            trade.qty,
            trade.price,
            trade.gross_amount,
            trade.fees_total,
            trade.status,
            trade.client_request_id,
            trade.revision,
        )
        for trade in session.scalars(select(Trade))
    }
    other_lots = {
        lot.id: (lot.remaining_qty, lot.remaining_cost, lot.cycle_id)
        for lot in session.scalars(select(Lot))
        if lot.security_id != security.id or lot.strategy_id != strategy.id
    }
    target_id = target.id
    old_lot_id = first.lot.id
    audit_count = session.scalar(select(func.count()).select_from(AuditEvent))
    # 直接模拟派生数据损坏，不能从残余字段当成事实重建。
    first.lot.remaining_qty = Decimal("1")
    first.lot.remaining_cost = Decimal("1")
    session.commit()
    begin_write(session)
    result = RecalculationService(session).rebuild(
        account_id=account.id,
        security_id=security.id,
        strategy_id=strategy.id,
    )
    session.commit()
    assert result == {"rebuiltLots": 2, "rebuiltMatches": 2}
    assert session.get(Lot, old_lot_id).remaining_qty == Decimal("7")
    assert session.get(Lot, second.lot.id).remaining_qty == Decimal("1")
    assert session.get(ProfitTarget, target_id).lot_id == old_lot_id
    assert session.scalar(select(func.count()).select_from(AuditEvent)) > audit_count
    assert {
        trade.id: (
            trade.qty,
            trade.price,
            trade.gross_amount,
            trade.fees_total,
            trade.status,
            trade.client_request_id,
            trade.revision,
        )
        for trade in session.scalars(select(Trade))
    } == original_trades
    assert {
        lot.id: (lot.remaining_qty, lot.remaining_cost, lot.cycle_id)
        for lot in session.scalars(select(Lot))
        if lot.id in other_lots
    } == other_lots
    first_snapshot = _snapshot(session)
    begin_write(session)
    RecalculationService(session).rebuild(
        account_id=account.id,
        security_id=security.id,
        strategy_id=strategy.id,
    )
    session.commit()
    assert _snapshot(session) == first_snapshot
    assert session.execute(text("PRAGMA foreign_key_check")).all() == []


def test_rebuild_failure_rolls_back_deletions_and_targets(ledger_context) -> None:
    """事实缺量时重建失败，事务回滚恢复原批次、匹配和目标。"""
    session, account, security, strategy = ledger_context
    buy = _buy(ledger_context, day=1, qty="10")
    sell = _sell(ledger_context, day=2, qty="5")
    session.add(
        ProfitTarget(
            lot_id=buy.lot.id,
            target_return_pct=Decimal("0.1"),
            planned_sell_qty=Decimal("1"),
            calculation_basis="NET",
            status="ACTIVE",
        )
    )
    sell.qty = Decimal("20")
    session.commit()
    before = _snapshot(session)
    with pytest.raises(InsufficientLotQtyError):
        begin_write(session)
        try:
            RecalculationService(session).rebuild(
                account_id=account.id,
                security_id=security.id,
                strategy_id=strategy.id,
            )
        except Exception:
            session.rollback()
            raise
    assert _snapshot(session) == before
    assert session.scalar(select(func.count()).select_from(ProfitTarget)) == 1


def test_dashboard_market_value_does_not_publish_partial_subtotal(ledger_context) -> None:
    """市值与同份估值一致，缺少任一持仓行情时不能展示部分合计。"""
    session, account, security, strategy = ledger_context
    _buy(ledger_context, day=1, qty="10")
    now = datetime.now(UTC)
    provider = MockQuoteProvider(
        {
            f"SH:{security.symbol}": QuoteSnapshot(
                symbol=f"SH:{security.symbol}",
                last_price=Decimal("12"),
                source_time=now,
                fetched_at=now,
                provider="test",
            )
        }
    )
    service = DashboardService(QuoteCollector(provider))
    complete = asyncio.run(service.build(session, account_id=account.id, mode="manual", now=now))
    assert complete["summary"]["marketValue"] == Decimal("120")
    other = Security(market="SZ", symbol="000001", name="平安银行")
    session.add(other)
    session.flush()
    _buy((session, account, other, strategy), day=1, qty="10")
    partial = asyncio.run(service.build(session, account_id=account.id, mode="cache", now=now))
    assert partial["summary"]["valuationStatus"] == "partial"
    assert partial["summary"]["marketValue"] is None


def test_equal_preview_and_confirmation_have_identical_profit_allocations(ledger_context) -> None:
    """自动等量预览与明确批次确认逐字段相同，原始成本差价计入收益。"""
    session, account, security, _ = ledger_context
    _buy(ledger_context, day=1, qty="10", price="10")
    _buy(ledger_context, day=2, qty="4", price="11")
    payload = {
        "accountId": account.id,
        "securityId": security.id,
        "executedAt": datetime(2026, 11, 3, tzinfo=UTC).isoformat(),
        "qty": "6",
        "price": "12",
        "commission": "0.05",
        "matchingMethod": "EQUAL",
    }
    with _api(session) as client:
        preview = client.post("/api/v1/trades/sell/preview", json=payload)
        assert preview.status_code == 200
        items = preview.json()["matches"]
        assert [Decimal(item["matchedQty"]) for item in items] == [Decimal("3"), Decimal("3")]
        payload["matches"] = [
            {"lotId": item["lotId"], "matchedQty": item["matchedQty"]} for item in items
        ]
        payload["clientRequestId"] = "equal-confirm"
        confirmed = client.post("/api/v1/trades/sell", json=payload)
        assert confirmed.status_code == 201
        actual = confirmed.json()["matches"]
        for field in ("lotId", "matchedQty", "allocatedBuyCost", "allocatedSellNet", "realizedPnl"):
            assert [item[field] for item in actual] == [item[field] for item in items]
        assert sum((Decimal(item["realizedPnl"]) for item in actual), Decimal("0")) == Decimal(
            "8.95"
        )


def test_rebuild_http_splits_cycles_skips_voided_and_preserves_other_account(
    ledger_context,
) -> None:
    """HTTP重建按有效事实重划周期、排除作废买入并隔离其他账户。"""
    session, account, security, strategy = ledger_context
    _buy(ledger_context, day=1, qty="10")
    _sell(ledger_context, day=2, qty="10")
    _buy(ledger_context, day=3, qty="6")
    cancelled = _buy(ledger_context, day=4, qty="2")
    TradeLedgerService(session).void_trade(cancelled.id)
    other_account = Account(name="独立账户", currency="CNY")
    session.add(other_account)
    session.flush()
    other_strategy = Strategy(account_id=other_account.id, name="DEFAULT")
    session.add(other_strategy)
    session.flush()
    other_buy = _buy((session, other_account, security, other_strategy), day=1, qty="50")
    other_lot_id = other_buy.lot.id
    original_updated = {trade.id: trade.updated_at for trade in session.scalars(select(Trade))}
    session.commit()
    with _api(session) as client:
        response = client.post(
            f"/api/v1/securities/{security.id}/recalculate?accountId={account.id}",
        )
        assert response.status_code == 200
        assert response.json() == {"rebuiltLots": 2, "rebuiltMatches": 1}
        lots = client.get(
            f"/api/v1/securities/{security.id}/lots?accountId={account.id}&includeClosed=true"
        ).json()["lots"]
        assert len(lots) == 2
        assert lots[0]["status"] == "OPEN"
        assert lots[1]["status"] == "CLOSED"
        assert lots[1]["unrealizedPnl"] is None
        assert lots[1]["effectiveTarget"] is None
    assert session.get(Lot, other_lot_id).remaining_qty == Decimal("50")
    cycles = list(session.scalars(select(Cycle).where(Cycle.account_id == account.id)))
    assert sorted(cycle.status for cycle in cycles) == ["CLOSED", "OPEN"]
    assert session.get(Trade, cancelled.id).status == "VOIDED"
    assert {
        trade.id: trade.updated_at for trade in session.scalars(select(Trade))
    } == original_updated
