from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from decimal import Decimal
import importlib

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.api.deps import get_db
from app.api.v1 import market as market_module
from app.db.base import Base
from app.db.session import create_engine_for_url
from app.main import create_app
from app.market_data.collector import QuoteCollector
from app.market_data.provider import MockQuoteProvider, QuoteSnapshot
from app.market_data.security_info import IntradayPoint, IntradaySnapshot
from tests.auth_helpers import attach_default_admin_auth

router_module = importlib.import_module("app.api.v1.router")


@contextmanager
def _build_client(
    monkeypatch: pytest.MonkeyPatch, provider: MockQuoteProvider
) -> Iterator[TestClient]:
    """创建内存 SQLite 与模拟行情源的端到端测试客户端。"""
    engine = create_engine_for_url("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, class_=Session, expire_on_commit=False)
    app = create_app()
    with factory() as seed_session:
        attach_default_admin_auth(app, seed_session)

    def override_db():
        """为每次测试请求提供独立 SQLite 会话。"""
        with factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_db
    monkeypatch.setattr(router_module, "quote_collector", QuoteCollector(provider))
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()
    engine.dispose()


def test_fund_proxy_binding_dashboard_lots_and_estimated_intraday_flow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """端到端覆盖场外基金绑定参考标的、看板/批次估算估值、估算分时与解绑恢复。"""
    now = datetime(2026, 11, 24, 6, 45, tzinfo=UTC)  # 北京时间 14:45
    prev_nav_time = datetime(2026, 11, 23, 7, 0, tzinfo=UTC)  # 北京时间 11-23 15:00

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

    with _build_client(monkeypatch, provider) as client:
        account = client.post(
            "/api/v1/accounts", json={"name": "A股基金账户", "currency": "CNY"}
        ).json()
        stock = client.post(
            "/api/v1/securities",
            json={"market": "SH", "symbol": "600519", "name": "贵州茅台", "securityType": "STOCK"},
        ).json()
        fund = client.post(
            "/api/v1/securities",
            json={"market": "OF", "symbol": "110020", "name": "易方达沪深300ETF联接A"},
        ).json()

        # 1. 非场外基金绑定参考标的应被拒绝
        bad_non_fund = client.patch(
            f"/api/v1/securities/{stock['id']}/proxy",
            json={"proxySymbol": "SH:000300", "proxyName": "沪深300"},
        )
        assert bad_non_fund.status_code == 422

        # 2. 非法参考标的代码或越界仓位系数应被拒绝
        bad_symbol = client.patch(
            f"/api/v1/securities/{fund['id']}/proxy",
            json={"proxySymbol": "US:SPY"},
        )
        assert bad_symbol.status_code == 422
        bad_ratio = client.patch(
            f"/api/v1/securities/{fund['id']}/proxy",
            json={"proxySymbol": "SH:000300", "trackingRatio": "2.0"},
        )
        assert bad_ratio.status_code == 422

        # 3. 正常绑定沪深300（不传 trackingRatio 默认 0.95）
        bound = client.patch(
            f"/api/v1/securities/{fund['id']}/proxy",
            json={"proxySymbol": "SH:000300", "proxyName": "沪深300"},
        )
        assert bound.status_code == 200
        bound_body = bound.json()
        assert bound_body["proxySymbol"] == "SH:000300"
        assert bound_body["proxyName"] == "沪深300"
        assert bound_body["trackingRatio"] == "0.950000"

        # 4. 买入 1000 份，单价 2.0000
        buy_res = client.post(
            "/api/v1/trades/buy",
            json={
                "accountId": account["id"],
                "securityId": fund["id"],
                "executedAt": "2026-11-23T06:30:00Z",
                "qty": "1000.0000",
                "price": "2.0000",
                "commission": "0",
            },
        )
        assert buy_res.status_code == 201

        # 5. Dashboard 手动刷新，返回盘中估算净值 2.038000 及浮盈 38.0000
        dash = client.get(
            "/api/v1/dashboard",
            params={"accountId": account["id"], "mode": "manual"},
        )
        assert dash.status_code == 200
        dash_body = dash.json()
        pos = dash_body["positions"][0]
        assert pos["quote"]["isEstimated"] is True
        assert Decimal(pos["quote"]["lastPrice"]) == Decimal("2.038000")
        assert Decimal(pos["quote"]["changePct"]) == Decimal("1.900000")
        assert Decimal(pos["quote"]["officialNav"]) == Decimal("2.000000")
        assert pos["quote"]["officialNavDate"] == "2026-11-23"
        assert pos["quote"]["proxySymbol"] == "SH:000300"
        assert Decimal(pos["unrealizedPnl"]) == Decimal("38.0000")

        # 6. Lots 接口同样使用估算净值计算单批浮盈
        lots_res = client.get(
            f"/api/v1/securities/{fund['id']}/lots",
            params={"accountId": account["id"]},
        )
        assert lots_res.status_code == 200
        lots_body = lots_res.json()
        assert lots_body["quote"]["isEstimated"] is True
        assert Decimal(lots_body["quote"]["lastPrice"]) == Decimal("2.038000")
        assert Decimal(lots_body["summary"]["unrealizedPnl"]) == Decimal("38.0000")

        # 7. Intraday 接口：series=auto 返回估算分时，series=nav 返回历史净值
        class StubIntradayService:
            """提供固定基金净值与参考指数分时的测试桩。"""

            async def read(self, symbol: str) -> IntradaySnapshot:
                """按标的返回历史净值或当日分钟分时。"""
                if symbol == "OF:110020":
                    return IntradaySnapshot(
                        symbol="OF:110020",
                        trading_date="2026-11-23",
                        points=(IntradayPoint(prev_nav_time, Decimal("2.000000")),),
                        quote=provider.quotes["OF:110020"],
                        fetched_at=now,
                        provider="eastmoney-fund",
                        series_kind="nav",
                    )
                return IntradaySnapshot(
                    symbol="SH:000300",
                    trading_date="2026-11-24",
                    points=(
                        IntradayPoint(datetime(2026, 11, 24, 1, 30, tzinfo=UTC), Decimal("4040.000000")),
                        IntradayPoint(now, Decimal("4080.000000")),
                    ),
                    quote=provider.quotes["SH:000300"],
                    fetched_at=now,
                    provider="eastmoney-intraday",
                    series_kind="intraday",
                )

        monkeypatch.setattr(market_module, "intraday_service", StubIntradayService())

        est_intraday = client.get(f"/api/v1/securities/{fund['id']}/intraday")
        assert est_intraday.status_code == 200
        est_body = est_intraday.json()
        assert est_body["seriesKind"] == "estimated_intraday"
        assert est_body["tradingDate"] == "2026-11-24"
        assert len(est_body["points"]) == 2
        # 第一点涨幅 +1% * 0.95 = +0.95% -> 2.0000 * 1.0095 = 2.019000
        assert est_body["points"][0]["price"] == "2.019000"
        assert est_body["points"][1]["price"] == "2.038000"
        assert est_body["quote"]["isEstimated"] is True

        nav_intraday = client.get(
            f"/api/v1/securities/{fund['id']}/intraday", params={"series": "nav"}
        )
        assert nav_intraday.status_code == 200
        assert nav_intraday.json()["seriesKind"] == "nav"

        # 8. 解绑后立即恢复官方净值展示
        unbound = client.patch(
            f"/api/v1/securities/{fund['id']}/proxy",
            json={"proxySymbol": None},
        )
        assert unbound.status_code == 200
        assert unbound.json()["proxySymbol"] is None
        lots_after = client.get(
            f"/api/v1/securities/{fund['id']}/lots",
            params={"accountId": account["id"]},
        ).json()
        assert lots_after["quote"]["isEstimated"] is False
        assert Decimal(lots_after["quote"]["lastPrice"]) == Decimal("2.000000")
        assert Decimal(lots_after["summary"]["unrealizedPnl"]) == Decimal("0.0000")
