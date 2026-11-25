"""SSE 实时行情流与内存优先估值集成测试。"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from decimal import Decimal
from importlib import import_module

import httpx
import pytest
from sqlalchemy.orm import Session, sessionmaker

from app.api.deps import get_current_user, get_db
from app.db.base import Base
from app.db.session import create_engine_for_url
from app.main import create_app
from app.market_data import MarketDataHub, MockQuoteProvider, QuoteCollector, QuoteSnapshot
from app.models.auth import User
from app.models.ledger import Account
from tests.auth_helpers import attach_default_admin_auth


def _parse_sse_frame(lines: list[str]) -> tuple[str | None, dict[str, object] | None]:
    """解析单帧 SSE 文本行，返回事件名与 JSON 数据。"""
    event_name: str | None = None
    data_lines: list[str] = []
    for line in lines:
        if line.startswith("event:"):
            event_name = line.split(":", 1)[1].strip()
        elif line.startswith("data:"):
            data_lines.append(line.split(":", 1)[1].strip())
    if not data_lines:
        return event_name, None
    return event_name, json.loads("\n".join(data_lines))


def test_quote_stream_requires_authentication() -> None:
    """未登录访问 /api/v1/quotes/stream 必须返回 401。"""
    engine = create_engine_for_url("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, class_=Session, expire_on_commit=False)
    application = create_app(create_tables=False)

    def override_db():
        with factory() as session:
            yield session

    application.dependency_overrides[get_db] = override_db

    async def _run() -> int:
        transport = httpx.ASGITransport(app=application)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/v1/quotes/stream")
            return response.status_code

    try:
        assert asyncio.run(_run()) == 401
    finally:
        engine.dispose()


def test_quote_stream_pushes_snapshot_incremental_quotes_and_isolates_users(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """登录建立 SSE 后应先收 snapshot、再收增量 quote，隔离用户并优先读内存。"""
    engine = create_engine_for_url("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, class_=Session, expire_on_commit=False)
    application = create_app(create_tables=False)

    def override_db():
        with factory() as session:
            yield session

    application.dependency_overrides[get_db] = override_db
    with factory() as setup_session:
        admin_user = attach_default_admin_auth(application, setup_session)
        other_user = User(id="u-other", email="other@example.com", role="user", status="active")
        setup_session.add(other_user)
        setup_session.flush()
        setup_session.add(
            Account(id="acc-other", user_id="u-other", name="他人账户", currency="CNY")
        )
        setup_session.commit()

    router_module = import_module("app.api.v1.router")
    provider = MockQuoteProvider()
    collector = QuoteCollector(provider)
    now = datetime.now(UTC)
    hub = MarketDataHub(
        quote_service=collector.service,
        provider=provider,
        session_factory=factory,
        broadcast_coalesce_seconds=0.0,
        now=lambda: now,
    )
    collector.service.bind_hub(hub)
    monkeypatch.setattr(router_module, "quote_collector", collector)
    monkeypatch.setattr(router_module, "quote_hub", hub)

    async def _run() -> None:
        transport = httpx.ASGITransport(app=application)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            # 1. 当前用户买入 SH:600519
            sec = (
                await client.post(
                    "/api/v1/securities",
                    json={"market": "SH", "symbol": "600519", "name": "贵州茅台"},
                )
            ).json()
            other_sec = (
                await client.post(
                    "/api/v1/securities",
                    json={"market": "SZ", "symbol": "000001", "name": "平安银行"},
                )
            ).json()
            buy_resp = await client.post(
                "/api/v1/trades/buy",
                json={
                    "securityId": sec["id"],
                    "executedAt": datetime(2026, 11, 1, tzinfo=UTC).isoformat(),
                    "qty": "10",
                    "price": "1500",
                },
            )
            assert buy_resp.status_code == 201

            # 另一用户在自己账户中加入 SZ:000001 自选
            application.dependency_overrides[get_current_user] = lambda: other_user
            wl_resp = await client.post(
                "/api/v1/watchlist",
                json={"securityId": other_sec["id"], "accountId": "acc-other"},
            )
            assert wl_resp.status_code == 201
            application.dependency_overrides[get_current_user] = lambda: admin_user

            # 先向内存发布初始报价
            hub.publish(
                {
                    "SH:600519": QuoteSnapshot(
                        symbol="SH:600519",
                        last_price=Decimal("1520.00"),
                        prev_close=Decimal("1500.00"),
                        source_time=now,
                        fetched_at=now,
                        provider="longbridge",
                    ),
                    "SZ:000001": QuoteSnapshot(
                        symbol="SZ:000001",
                        last_price=Decimal("11.00"),
                        prev_close=Decimal("10.00"),
                        source_time=now,
                        fetched_at=now,
                        provider="longbridge",
                    ),
                }
            )

            # 2. 验证 /dashboard?mode=cache 直接命中内存最新报价，浮盈 = (1520-1500)*10 = 200
            dash_resp = await client.get("/api/v1/dashboard?mode=cache")
            assert dash_resp.status_code == 200
            dash = dash_resp.json()
            assert dash["summary"]["unrealizedPnl"] == "200.0000"
            assert dash["summary"]["valuationStatus"] == "fresh"

            # 3. 建立 SSE 流：订阅建立后推送增量报价并关闭流，验证 snapshot/quote 与用户隔离
            async def _push_and_close() -> None:
                for _ in range(50):
                    if hub._subscribers:
                        break
                    await asyncio.sleep(0.01)
                hub.publish(
                    {
                        "SZ:000001": QuoteSnapshot(
                            symbol="SZ:000001",
                            last_price=Decimal("11.50"),
                            prev_close=Decimal("10.00"),
                            source_time=now,
                            fetched_at=now,
                            provider="longbridge",
                        ),
                        "SH:600519": QuoteSnapshot(
                            symbol="SH:600519",
                            last_price=Decimal("1535.00"),
                            prev_close=Decimal("1500.00"),
                            source_time=now,
                            fetched_at=now,
                            provider="longbridge",
                        ),
                    }
                )
                await asyncio.sleep(0.02)
                hub.close_all_subscribers()

            pusher = asyncio.create_task(_push_and_close())
            stream_resp = await client.get("/api/v1/quotes/stream")
            await pusher

            assert stream_resp.status_code == 200
            assert "text/event-stream" in stream_resp.headers["content-type"]
            assert stream_resp.headers.get("x-accel-buffering") == "no"

            frames: list[tuple[str | None, dict[str, object] | None]] = []
            buf: list[str] = []
            for line in stream_resp.text.splitlines():
                if line == "":
                    if buf:
                        frames.append(_parse_sse_frame(buf))
                        buf = []
                else:
                    buf.append(line)
            if buf:
                frames.append(_parse_sse_frame(buf))

            assert len(frames) >= 2
            ev1, data1 = frames[0]
            assert ev1 == "snapshot"
            assert data1 is not None
            snap_quotes = data1["quotes"]
            assert isinstance(snap_quotes, list)
            assert [q["symbol"] for q in snap_quotes] == ["SH:600519"]
            assert snap_quotes[0]["lastPrice"] == "1520.000000"

            ev2, data2 = frames[1]
            assert ev2 == "quote"
            assert data2 is not None
            inc_quotes = data2["quotes"]
            assert isinstance(inc_quotes, list)
            assert [q["symbol"] for q in inc_quotes] == ["SH:600519"]
            assert inc_quotes[0]["lastPrice"] == "1535.000000"
            assert not hub._subscribers

    try:
        asyncio.run(_run())
    finally:
        engine.dispose()
