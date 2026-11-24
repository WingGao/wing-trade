from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.domain.enums import TargetStatus
from app.market_data.collector import QuoteCollector
from app.market_data.provider import MockQuoteProvider, QuoteSnapshot
from app.market_data.security_info import SecurityInfoProvider
from app.market_data.service import QuoteService
from app.models.ledger import Account, Base, Cycle, Lot, ProfitTarget, QuoteCache, Security, Strategy, Trade


def _memory_session():
    """创建内存 SQLite 会话用于隔离验证行情估算与缓存契约。"""
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, future=True)()


def test_security_search_recognizes_ashare_indices() -> None:
    """外部查名能正确识别沪深宽基指数为 INDEX 并保留 SH/SZ 市场。"""
    payload = {
        "QuotationCodeTable": {
            "Data": [
                {
                    "Code": "000300",
                    "Name": "沪深300",
                    "MktNum": "1",
                    "Classify": "INDEX",
                    "SecurityTypeName": "指数",
                    "QuoteID": "1.000300",
                },
                {
                    "Code": "399006",
                    "Name": "创业板指",
                    "MktNum": "0",
                    "Classify": "INDEX",
                    "SecurityTypeName": "指数",
                    "QuoteID": "0.399006",
                },
                {
                    "Code": "510300",
                    "Name": "沪深300ETF",
                    "MktNum": "1",
                    "Classify": "AStock",
                    "SecurityTypeName": "基金",
                    "QuoteID": "1.510300",
                },
            ]
        }
    }

    def transport(_request, _timeout):
        """返回包含沪深指数与场内 ETF 的模拟查名响应。"""
        return json.dumps(payload, ensure_ascii=False).encode()

    provider = SecurityInfoProvider(transport=transport)
    candidates = provider.search("000300")
    by_key = {(item.market, item.symbol): item for item in candidates}

    assert by_key[("SH", "000300")].security_type == "INDEX"
    assert by_key[("SH", "000300")].name == "沪深300"
    assert by_key[("SZ", "399006")].security_type == "INDEX"
    assert by_key[("SH", "510300")].security_type == "STOCK"


def test_quote_service_estimates_intraday_nav_and_switches_back_on_official_nav() -> None:
    """官方净值早于参考标的交易日时按公式估算；当日官方净值公布后自动切回官方净值。"""
    service = QuoteService()
    fund = Security(
        market="OF",
        symbol="110020",
        name="易方达沪深300ETF联接A",
        security_type="FUND",
        proxy_symbol="SH:000300",
        proxy_name="沪深300",
        tracking_ratio=Decimal("0.950000"),
    )
    prev_nav_time = datetime(2026, 11, 23, 7, 0, tzinfo=UTC)  # 北京时间 2026-11-23 15:00
    today_intraday = datetime(2026, 11, 24, 6, 45, tzinfo=UTC)  # 北京时间 2026-11-24 14:45
    today_nav_time = datetime(2026, 11, 24, 7, 0, tzinfo=UTC)  # 北京时间 2026-11-24 15:00

    official_prev = QuoteSnapshot(
        symbol="OF:110020",
        last_price=Decimal("2.000000"),
        prev_close=Decimal("1.980000"),
        change_pct=Decimal("1.010101"),
        source_time=prev_nav_time,
        fetched_at=today_intraday,
        provider="eastmoney-fund",
    )
    proxy_live = QuoteSnapshot(
        symbol="SH:000300",
        last_price=Decimal("4080.000000"),
        prev_close=Decimal("4000.000000"),
        change_pct=Decimal("2.000000"),
        source_time=today_intraday,
        fetched_at=today_intraday,
        provider="eastmoney",
        market_session="regular",
    )

    # 1. 官方净值日期(11-23) < 参考标的日期(11-24)：启用盘中估算
    # 估算涨跌幅 = 2.00 * 0.95 = 1.90%，估算净值 = 2.0000 * 1.019 = 2.038000
    estimated = service.resolve_effective_snapshot(fund, official_prev, proxy_live)
    assert estimated is not None
    assert estimated.is_estimated is True
    assert estimated.change_pct == Decimal("1.900000")
    assert estimated.last_price == Decimal("2.038000")
    assert estimated.prev_close == Decimal("2.000000")
    assert estimated.official_nav == Decimal("2.000000")
    assert estimated.official_nav_date == "2026-11-23"
    assert estimated.proxy_symbol == "SH:000300"
    assert estimated.proxy_name == "沪深300"
    assert estimated.proxy_change_pct == Decimal("2.000000")
    assert estimated.tracking_ratio == Decimal("0.950000")

    # 2. 当晚官方净值公布为 11-24：自动切回官方净值，不重复叠乘
    official_today = QuoteSnapshot(
        symbol="OF:110020",
        last_price=Decimal("2.036500"),
        prev_close=Decimal("2.000000"),
        change_pct=Decimal("1.825000"),
        source_time=today_nav_time,
        fetched_at=datetime(2026, 11, 24, 13, 0, tzinfo=UTC),
        provider="eastmoney-fund",
    )
    resolved_today = service.resolve_effective_snapshot(fund, official_today, proxy_live)
    assert resolved_today is not None
    assert resolved_today.is_estimated is False
    assert resolved_today.last_price == Decimal("2.036500")
    assert resolved_today.official_nav == Decimal("2.036500")
    assert resolved_today.official_nav_date == "2026-11-24"

    # 3. 参考标的缺失时平滑降级回官方净值
    fallback = service.resolve_effective_snapshot(fund, official_prev, None)
    assert fallback is not None
    assert fallback.is_estimated is False
    assert fallback.last_price == Decimal("2.000000")


def test_collector_fetches_proxy_and_does_not_persist_estimated_target_trigger() -> None:
    """刷新场外基金时自动附带采集参考标的，且盘中估算触达止盈价不改写数据库 TRIGGERED 状态。"""
    session = _memory_session()
    now = datetime(2026, 11, 24, 6, 45, tzinfo=UTC)  # 北京时间 14:45 盘中
    prev_nav_time = datetime(2026, 11, 23, 7, 0, tzinfo=UTC)

    account = Account(name="A股账户", currency="CNY")
    session.add(account)
    session.flush()
    strategy = Strategy(account_id=account.id, name="DEFAULT")
    session.add(strategy)
    proxy_sec = Security(market="SH", symbol="000300", name="沪深300", security_type="INDEX")
    fund_sec = Security(
        market="OF",
        symbol="110020",
        name="沪深300联接A",
        security_type="FUND",
        proxy_symbol="SH:000300",
        proxy_name="沪深300",
        tracking_ratio=Decimal("0.950000"),
    )
    session.add_all([proxy_sec, fund_sec])
    session.flush()

    cycle = Cycle(
        account_id=account.id,
        security_id=fund_sec.id,
        strategy_id=strategy.id,
        opened_at=prev_nav_time,
    )
    session.add(cycle)
    session.flush()
    trade = Trade(
        account_id=account.id,
        security_id=fund_sec.id,
        strategy_id=strategy.id,
        cycle_id=cycle.id,
        side="BUY",
        executed_at=prev_nav_time,
        qty=Decimal("1000.0000"),
        price=Decimal("2.000000"),
        gross_amount=Decimal("2000.0000"),
    )
    session.add(trade)
    session.flush()
    lot = Lot(
        buy_trade_id=trade.id,
        account_id=account.id,
        security_id=fund_sec.id,
        strategy_id=strategy.id,
        cycle_id=cycle.id,
        original_qty=Decimal("1000.0000"),
        original_cost=Decimal("2000.0000"),
        remaining_qty=Decimal("1000.0000"),
        remaining_cost=Decimal("2000.0000"),
        unit_cost=Decimal("2.000000"),
        executed_at=prev_nav_time,
    )
    session.add(lot)
    session.flush()
    # 设定止盈目标价为 2.03：官方净值 2.00 未达标，但盘中估算净值 2.038 已达标
    target = ProfitTarget(
        lot_id=lot.id,
        target_return_pct=Decimal("0.015000"),
        planned_sell_qty=Decimal("1000.0000"),
        target_price_estimate=Decimal("2.030000"),
        alert_enabled=True,
        status=TargetStatus.ACTIVE.value,
    )
    session.add(target)
    session.commit()

    provider = MockQuoteProvider(
        {
            "OF:110020": QuoteSnapshot(
                symbol="OF:110020",
                last_price=Decimal("2.000000"),
                prev_close=Decimal("1.980000"),
                change_pct=Decimal("1.010101"),
                source_time=prev_nav_time,
                fetched_at=now,
                provider="eastmoney-fund",
            ),
            "SH:000300": QuoteSnapshot(
                symbol="SH:000300",
                last_price=Decimal("4080.000000"),
                prev_close=Decimal("4000.000000"),
                change_pct=Decimal("2.000000"),
                source_time=now,
                fetched_at=now,
                provider="eastmoney",
                market_session="regular",
            ),
        }
    )
    collector = QuoteCollector(provider)
    snapshots = asyncio.run(collector.refresh(session, [fund_sec], now=now))
    assert len(snapshots) == 1
    assert snapshots[0].is_estimated is True
    assert snapshots[0].last_price == Decimal("2.038000")

    # 原始 quote_cache 中场外基金仍保留官方净值 2.000000，绝不被估算价污染
    raw_cache = session.get(QuoteCache, fund_sec.id)
    assert raw_cache is not None
    assert raw_cache.last_price == Decimal("2.000000")

    # 止盈目标在数据库中仍保持 ACTIVE，不因盘中估算提前固化
    reloaded_target = session.scalar(select(ProfitTarget).where(ProfitTarget.id == target.id))
    assert reloaded_target is not None
    assert reloaded_target.status == TargetStatus.ACTIVE.value
