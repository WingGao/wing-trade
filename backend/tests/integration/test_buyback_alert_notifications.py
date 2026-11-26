"""关注标的与目标买回价企业微信推送引擎集成测试（隔离临时库）。"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.domain.enums import UserRole, UserStatus
from app.market_data.hub import MarketDataHub
from app.market_data.provider import MockQuoteProvider, QuoteSnapshot
from app.market_data.service import QuoteService
from app.models import (
    Account,
    Base,
    BuybackAlertDelivery,
    NotificationChannel,
    Security,
    Strategy,
    User,
    WatchlistItem,
)
from app.services.ledger import TradeLedgerService
from app.services.notifications.channels import WECOM_CHANNEL_TYPE
from app.services.notifications.engine import ProfitAlertEngine

VALID_WEBHOOK = "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=buybackTestKey123456"


class _FakeChannel:
    """记录企微推送内容的轻量替身通道。"""

    channel_type = WECOM_CHANNEL_TYPE

    def __init__(self) -> None:
        """初始化推送记录列表。"""
        self.sent: list[tuple[str, str]] = []

    def send_markdown(self, target: str, content: str) -> None:
        """记录收到的 Markdown 推送内容。"""
        self.sent.append((target, content))


def test_buyback_alert_triggers_for_cleared_and_holding_and_dedups(tmp_path: Path) -> None:
    """验证目标买回价在空仓与持仓中均触发推送、同日同价去重、改价重触发及闭市不误报。"""
    db_file = tmp_path / "buyback-alert.sqlite"
    engine_db = create_engine(
        f"sqlite:///{db_file.as_posix()}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine_db)
    session_factory = sessionmaker(bind=engine_db, autoflush=False, expire_on_commit=False)

    with session_factory() as session:
        user = User(
            email="buyback@example.com",
            display_name="波段用户",
            role=UserRole.ADMIN.value,
            status=UserStatus.ACTIVE.value,
            is_default_admin=True,
        )
        session.add(user)
        session.flush()
        session.add(
            NotificationChannel(
                user_id=user.id,
                channel_type=WECOM_CHANNEL_TYPE,
                enabled=True,
                webhook_url=VALID_WEBHOOK,
            )
        )
        account = Account(user_id=user.id, name="A股主账户", currency="CNY")
        session.add(account)
        session.flush()
        strategy = Strategy(account_id=account.id, name="默认策略")
        sec = Security(market="SH", symbol="600519", name="贵州茅台", security_type="STOCK")
        session.add_all([strategy, sec])
        session.flush()
        watch = WatchlistItem(
            account_id=account.id,
            security_id=sec.id,
            sort_order=0,
            target_buy_price=Decimal("1420.000000"),
            alert_enabled=True,
            note="分批接回第一笔",
        )
        session.add(watch)
        session.commit()
        account_id, strategy_id, sec_id, watch_id = account.id, strategy.id, sec.id, watch.id

    provider = MockQuoteProvider()
    quote_service = QuoteService()
    hub = MarketDataHub(
        provider=provider,
        quote_service=quote_service,
        session_factory=session_factory,
    )
    fake_channel = _FakeChannel()
    alert_engine = ProfitAlertEngine(
        quote_hub=hub,
        session_factory=session_factory,
        channels={WECOM_CHANNEL_TYPE: fake_channel},
        debounce_seconds=0.0,
    )

    # 2026-11-26 10:30 北京时间（周四 A 股盘中） -> 02:30 UTC
    trading_now = datetime(2026, 11, 26, 2, 30, tzinfo=UTC)
    closed_now = datetime(2026, 11, 26, 12, 0, tzinfo=UTC)  # 20:00 北京时间（已闭市）

    def set_quote(price: str, *, is_stale: bool = False) -> None:
        """向内存行情中心注入指定测试价格。"""
        hub._quotes["SH:600519"] = QuoteSnapshot(
            symbol="SH:600519",
            last_price=Decimal(price),
            prev_close=Decimal("1450.00"),
            change_pct=Decimal("-0.02"),
            source_time=trading_now,
            fetched_at=trading_now,
            provider="mock",
            is_stale=is_stale,
            market_session="regular",
        )

    # 1. 现价高于目标买回价 -> 不触发
    set_quote("1430.00")
    assert asyncio.run(alert_engine.evaluate_and_dispatch(["SH:600519"], now=trading_now)) == 0
    assert len(fake_channel.sent) == 0

    # 2. 闭市时段即使命中价格也不误报
    set_quote("1400.00")
    assert asyncio.run(alert_engine.evaluate_and_dispatch(["SH:600519"], now=closed_now)) == 0
    assert len(fake_channel.sent) == 0

    # 3. 过期行情不误报
    set_quote("1400.00", is_stale=True)
    assert asyncio.run(alert_engine.evaluate_and_dispatch(["SH:600519"], now=trading_now)) == 0
    assert len(fake_channel.sent) == 0

    # 4. 空仓盘中命中 -> 推送 1 次，包含「空仓待回接」与备注
    set_quote("1400.00", is_stale=False)
    assert asyncio.run(alert_engine.evaluate_and_dispatch(["SH:600519"], now=trading_now)) == 1
    assert len(fake_channel.sent) == 1
    assert "买回价提醒" in fake_channel.sent[0][1]
    assert "空仓待回接" in fake_channel.sent[0][1]
    assert "-1.41%" in fake_channel.sent[0][1]
    assert "分批接回第一笔" in fake_channel.sent[0][1]

    # 5. 同日同目标价再次评估 -> 幂等去重，不再推送
    assert asyncio.run(alert_engine.evaluate_and_dispatch(["SH:600519"], now=trading_now)) == 0
    assert len(fake_channel.sent) == 1

    # 6. 买入变为持仓中并下调目标买回价至 1390，现价跌至 1385 -> 持仓中依然触发推送
    with session_factory() as session:
        TradeLedgerService(session).buy(
            account_id=account_id,
            security_id=sec_id,
            strategy_id=strategy_id,
            qty=Decimal("100"),
            price=Decimal("1400"),
            executed_at=datetime(2026, 11, 26, 2, 35, tzinfo=UTC),
            commission=Decimal("0"),
            other_fee=Decimal("0"),
        )
        watch_row = session.get(WatchlistItem, watch_id)
        assert watch_row is not None
        watch_row.target_buy_price = Decimal("1390.000000")
        session.commit()

    set_quote("1385.00", is_stale=False)
    assert asyncio.run(alert_engine.evaluate_and_dispatch(["SH:600519"], now=trading_now)) == 1
    assert len(fake_channel.sent) == 2
    assert "持仓中（低位加仓参考）" in fake_channel.sent[1][1]

    with session_factory() as session:
        deliveries = list(session.scalars(select(BuybackAlertDelivery)).all())
        assert len(deliveries) == 2
