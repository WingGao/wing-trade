from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.api.deps import get_db
from app.api.v1 import market
from app.db.base import Base
from app.db.session import create_engine_for_url
from app.main import create_app
from app.market_data.security_info import IntradayService, SecurityInfoProvider


@pytest.fixture
def fund_client(monkeypatch):
    """构建内存 SQLite + 注入场外基金外部源的端到端测试客户端。"""
    engine = create_engine_for_url("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, class_=Session, expire_on_commit=False)
    now = datetime.now(UTC)

    def transport(request, _timeout):
        """响应场外基金查名与近期已公布净值请求。"""
        if "suggest/get" in request.full_url:
            return json.dumps(
                {
                    "QuotationCodeTable": {
                        "Data": [
                            {
                                "Code": "005827",
                                "Name": "易方达蓝筹精选混合",
                                "MktNum": "",
                                "Classify": "Fund",
                                "SecurityTypeName": "开放式基金",
                                "QuoteID": "7.005827",
                            }
                        ]
                    }
                },
                ensure_ascii=False,
            ).encode()
        return json.dumps(
            {
                "Data": {
                    "LSJZList": [
                        {"FSRQ": "2026-11-20", "DWJZ": "2.0000", "JZZZL": "1.52"},
                        {"FSRQ": "2026-11-19", "DWJZ": "1.9700", "JZZZL": "-0.30"},
                    ]
                },
                "ErrCode": 0,
            },
            ensure_ascii=False,
        ).encode()

    provider = SecurityInfoProvider(transport=transport, now=lambda: now)
    monkeypatch.setattr(market, "security_info_provider", provider)
    monkeypatch.setattr(market, "intraday_service", IntradayService(provider))
    application = create_app(create_tables=False)

    def override_db():
        """每个 HTTP 请求独立会话。"""
        with factory() as session:
            yield session

    from tests.auth_helpers import attach_default_admin_auth

    application.dependency_overrides[get_db] = override_db
    with factory() as setup_session:
        attach_default_admin_auth(application, setup_session)
    with TestClient(application) as client:
        yield client
    engine.dispose()


def test_open_fund_lookup_fractional_trade_nav_and_dashboard_flow(fund_client) -> None:
    """端到端校验场外基金查名、建档、小数份额买卖、近期净值曲线与首页估值。"""
    lookup = fund_client.get("/api/v1/securities/lookup?query=005827")
    assert lookup.status_code == 200
    candidates = lookup.json()["securities"]
    assert len(candidates) == 1
    assert candidates[0]["market"] == "OF"
    assert candidates[0]["symbol"] == "005827"
    assert candidates[0]["securityType"] == "FUND"
    assert candidates[0]["currency"] == "CNY"
    assert candidates[0]["id"] is None

    # 建档未显式传 securityType 时，OF 市场自动规范为 FUND
    created = fund_client.post(
        "/api/v1/securities",
        json={"market": "OF", "symbol": "005827", "name": "易方达蓝筹精选混合"},
    )
    assert created.status_code == 201
    security = created.json()
    assert security["market"] == "OF"
    assert security["securityType"] == "FUND"
    assert security["currency"] == "CNY"

    # 录入小数份额 + 四位单位净值买入
    buy = fund_client.post(
        "/api/v1/trades/buy",
        json={
            "securityId": security["id"],
            "executedAt": "2026-11-18T07:00:00Z",
            "qty": "1234.56",
            "price": "1.5000",
            "commission": "1.50",
        },
    )
    assert buy.status_code == 201
    assert buy.json()["qty"] == "1234.5600"
    assert buy.json()["price"] == "1.500000"
    assert buy.json()["securityType"] == "FUND"

    # 预览并提交小数份额赎回
    preview = fund_client.post(
        "/api/v1/trades/sell/preview",
        json={
            "securityId": security["id"],
            "executedAt": "2026-11-19T07:00:00Z",
            "qty": "234.56",
            "price": "1.8000",
            "matchingMethod": "EQUAL",
        },
    )
    assert preview.status_code == 200
    matches = preview.json()["matches"]
    assert len(matches) == 1
    assert matches[0]["matchedQty"] == "234.5600"

    sell = fund_client.post(
        "/api/v1/trades/sell",
        json={
            "securityId": security["id"],
            "executedAt": "2026-11-19T07:00:00Z",
            "qty": "234.56",
            "price": "1.8000",
            "matches": [{"lotId": matches[0]["lotId"], "matchedQty": matches[0]["matchedQty"]}],
        },
    )
    assert sell.status_code == 201

    # 获取近期已公布单位净值曲线并同步写入共享缓存
    nav_res = fund_client.get(f"/api/v1/securities/{security['id']}/intraday")
    assert nav_res.status_code == 200
    nav = nav_res.json()
    assert nav["seriesKind"] == "nav"
    assert nav["tradingDate"] == "2026-11-20"
    assert [p["price"] for p in nav["points"]] == ["1.970000", "2.000000"]
    assert nav["quote"]["lastPrice"] == "2.000000"
    assert nav["isStale"] is False

    # Dashboard 读取缓存估值：剩余 1000 份，最新净值 2.0000，且盘中不会被误判过期
    dashboard = fund_client.get("/api/v1/dashboard?mode=cache")
    assert dashboard.status_code == 200
    dash = dashboard.json()
    assert dash["summary"]["valuationStatus"] == "fresh"
    assert len(dash["positions"]) == 1
    pos = dash["positions"][0]
    assert pos["market"] == "OF"
    assert pos["securityType"] == "FUND"
    assert pos["remainingQty"] == "1000.0000"
    assert pos["quote"]["lastPrice"] == "2.000000"
    assert pos["quote"]["isStale"] is False

    # 流水列表携带 securityType
    trades = fund_client.get("/api/v1/trades").json()
    assert len(trades) == 2
    assert all(item["securityType"] == "FUND" for item in trades)
