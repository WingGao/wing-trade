from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal

import importlib

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.api.deps import get_db
from app.api.v1 import market
from app.db.base import Base
from app.db.session import create_engine_for_url
from app.main import create_app
from app.market_data.calendar import USTradingCalendar
from app.market_data.collector import QuoteCollector
from app.market_data.provider import EastmoneyUSQuoteProvider, USMarketResolver
from app.market_data.security_info import IntradayService, SecurityInfoProvider


def _us_dt(year: int, month: int, day: int, hour: int, minute: int) -> datetime:
    """构造带美东时区的测试时间并转换为 UTC。"""
    return datetime(year, month, day, hour, minute, tzinfo=USTradingCalendar.timezone).astimezone(UTC)


@pytest.fixture
def us_client(monkeypatch):
    """构建内存 SQLite + 注入美股行情与分时源的端到端测试客户端。"""
    engine = create_engine_for_url("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, class_=Session, expire_on_commit=False)
    # 固定在美股盘前时段：2026-11-23 08:15 ET（北京时间 2026-11-23 21:15）
    pre_now = _us_dt(2026, 11, 23, 8, 15)
    resolver = USMarketResolver()
    resolver.remember("AAPL", "105")

    def transport(request, _timeout):
        """响应美股查名、盘前批量行情与盘前分时请求。"""
        url = request.full_url
        if "suggest/get" in url:
            return json.dumps(
                {
                    "QuotationCodeTable": {
                        "Data": [
                            {"Code": "AAPL", "Name": "苹果", "MktNum": "105", "Classify": "UsStock"}
                        ]
                    }
                },
                ensure_ascii=False,
            ).encode()
        if "ulist.np/get" in url:
            return json.dumps(
                {
                    "data": {
                        "diff": [
                            {
                                "f12": "AAPL",
                                "f13": 105,
                                "f2": 220.00,
                                "f3": 0.00,
                                "f18": 220.00,
                                "f124": int(pre_now.timestamp()),
                                "f251": 240.00,
                                "f253": 9.09,
                            }
                        ]
                    }
                }
            ).encode()
        return json.dumps(
            {
                "data": {
                    "code": "AAPL",
                    "market": 105,
                    "preClose": 220.00,
                    "trends": [
                        "2026-11-23 21:10,238.00,239.00,239.00,238.00,100,23850,238.50",
                        "2026-11-23 21:15,239.00,240.00,240.00,239.00,120,28740,239.50",
                    ],
                }
            }
        ).encode()

    info_provider = SecurityInfoProvider(transport=transport, now=lambda: pre_now, us_resolver=resolver)
    us_quote_provider = EastmoneyUSQuoteProvider(
        transport=transport,
        now=lambda: pre_now,
        resolver=resolver,
    )
    collector = QuoteCollector(us_quote_provider)
    api_router_module = importlib.import_module("app.api.v1.router")
    monkeypatch.setattr(market, "security_info_provider", info_provider)
    monkeypatch.setattr(market, "intraday_service", IntradayService(info_provider))
    monkeypatch.setattr(api_router_module, "quote_collector", collector)

    # 固定 DashboardService 默认时钟为美股盘前，验证盘前活跃状态与 10 秒轮询
    from app.services import dashboard as dashboard_module

    original_build = dashboard_module.DashboardService.build

    async def build_at_pre_now(self, session, *, account_id, include_closed=False, mode="auto", now=None):
        return await original_build(
            self,
            session,
            account_id=account_id,
            include_closed=include_closed,
            mode=mode,
            now=now or pre_now,
        )

    monkeypatch.setattr(dashboard_module.DashboardService, "build", build_at_pre_now)

    application = create_app(create_tables=False)

    def override_db():
        """每个 HTTP 请求提供独立数据库会话。"""
        with factory() as session:
            yield session

    from tests.auth_helpers import attach_default_admin_auth

    application.dependency_overrides[get_db] = override_db
    with factory() as setup_session:
        attach_default_admin_auth(application, setup_session)
    with TestClient(application) as client:
        yield client
    engine.dispose()


def test_us_stock_account_fractional_trade_premarket_dashboard_and_intraday(us_client) -> None:
    """端到端校验 USD 账户、碎股含费买卖、美股盘前总览估值、流水筛选与分时曲线。"""
    # 1. 创建 USD 账户
    account_resp = us_client.post(
        "/api/v1/accounts",
        json={"name": "盈透美股", "currency": "USD", "broker": "IBKR"},
    )
    assert account_resp.status_code == 201
    usd_account = account_resp.json()
    assert usd_account["currency"] == "USD"
    account_id = usd_account["id"]

    # 2. 查名并建档美股
    lookup = us_client.get("/api/v1/securities/lookup?query=AAPL")
    assert lookup.status_code == 200
    assert any(item["market"] == "US" and item["symbol"] == "AAPL" for item in lookup.json()["securities"])

    created_sec = us_client.post(
        "/api/v1/securities",
        json={"market": "US", "symbol": "AAPL", "name": "苹果"},
    )
    assert created_sec.status_code == 201
    security_id = created_sec.json()["id"]

    # 3. 碎股买入：1.25 股 @ $200.00，佣金 $1.00，其他费 $0.50 -> 含费总成本 $251.50
    buy_resp = us_client.post(
        "/api/v1/trades/buy",
        json={
            "accountId": account_id,
            "securityId": security_id,
            "qty": "1.25",
            "price": "200.00",
            "executedAt": "2026-11-20T15:00:00Z",
            "commission": "1.00",
            "otherFee": "0.50",
            "clientRequestId": "us-buy-1",
        },
    )
    assert buy_resp.status_code == 201

    # 4. 碎股含费卖出预览与提交：0.50 股 @ $220.00，佣金 $1.00，其他费 $0.25
    preview_resp = us_client.post(
        "/api/v1/trades/sell/preview",
        json={
            "accountId": account_id,
            "securityId": security_id,
            "qty": "0.50",
            "price": "220.00",
            "executedAt": "2026-11-20T16:00:00Z",
            "commission": "1.00",
            "otherFee": "0.25",
            "matchingMethod": "EQUAL",
        },
    )
    assert preview_resp.status_code == 200
    preview = preview_resp.json()
    assert preview["currency"] == "USD"
    assert Decimal(preview["feesTotal"]) == Decimal("1.2500")
    assert Decimal(preview["cashIn"]) == Decimal("108.7500")

    sell_resp = us_client.post(
        "/api/v1/trades/sell",
        json={
            "accountId": account_id,
            "securityId": security_id,
            "qty": "0.50",
            "price": "220.00",
            "executedAt": "2026-11-20T16:00:00Z",
            "commission": "1.00",
            "otherFee": "0.25",
            "clientRequestId": "us-sell-1",
            "matches": [{"lotId": preview["matches"][0]["lotId"], "matchedQty": "0.50"}],
        },
    )
    assert sell_resp.status_code == 201

    # 5. USD 账户总览：美股盘前时段活跃（10s 轮询）+ 盘前现价 $240.00 估值
    dash_resp = us_client.get(f"/api/v1/dashboard?accountId={account_id}&mode=manual")
    assert dash_resp.status_code == 200
    dash = dash_resp.json()
    assert dash["currency"] == "USD"
    assert dash["marketSessionOpen"] is True
    assert dash["marketSession"] == "pre"
    assert dash["pollAfterSeconds"] == 10
    assert dash["summary"]["valuationStatus"] == "fresh"
    assert len(dash["positions"]) == 1
    pos = dash["positions"][0]
    assert Decimal(pos["remainingQty"]) == Decimal("0.7500")
    assert Decimal(pos["quote"]["lastPrice"]) == Decimal("240.000000")
    assert pos["quote"]["marketSession"] == "pre"

    # 6. 按账户筛选流水与复盘汇总
    trades_resp = us_client.get(f"/api/v1/trades?accountId={account_id}")
    assert trades_resp.status_code == 200
    assert len(trades_resp.json()) == 2
    assert all(row["currency"] == "USD" for row in trades_resp.json())

    summary_resp = us_client.get(f"/api/v1/analytics/summary?accountId={account_id}")
    assert summary_resp.status_code == 200
    assert summary_resp.json()["currency"] == "USD"
    assert Decimal(summary_resp.json()["remainingQty"]) == Decimal("0.7500")

    # 7. 美股盘前分时曲线
    intraday_resp = us_client.get(f"/api/v1/securities/{security_id}/intraday")
    assert intraday_resp.status_code == 200
    intraday = intraday_resp.json()
    assert intraday["tradingDate"] == "2026-11-23"
    assert len(intraday["points"]) == 2
    assert intraday["quote"]["marketSession"] == "pre"
    assert Decimal(intraday["quote"]["lastPrice"]) == Decimal("240.000000")
